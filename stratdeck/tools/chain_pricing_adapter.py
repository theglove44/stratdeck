# stratdeck/tools/chain_pricing_adapter.py

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional, Sequence, List

from .chains import get_chain, _nearest_expiry
from .dates import compute_dte
from .pricing import pop_estimate, vertical_credit
from .retries import call_with_retries

log = logging.getLogger(__name__)


class ChainPricingAdapter:
    """
    Thin adapter that bridges TradePlanner to chain/pricing helpers.

    Responsibilities:
    - Expose get_available_dtes(symbol) for StrategyEngine DTE selection.
    - Given a symbol, target DTE, and a set of legs, compute:
        * estimated total credit
        * credit_per_width
        * a simple POP estimate

    For now this only supports simple vertical spreads (short put / call spreads)
    where legs are a small list of TradeLeg-like objects with attributes:
      - side ("short"/"long")
      - type ("put"/"call")
      - strike (float)
      - expiry (ignored here; we use DTE -> expiry mapping)
    """

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _extract_delta(q: Dict[str, Any]) -> float:
        """
        Best-effort extraction of short-leg delta from a chain row.

        Handles:
          - top-level 'delta'
          - nested 'greeks': {'delta': ...}
        """
        if not isinstance(q, dict):
            return 0.0
        val = q.get("delta")
        if val is None:
            greeks = q.get("greeks")
            if isinstance(greeks, dict):
                val = greeks.get("delta")
        try:
            return abs(float(val))
        except Exception:
            return 0.0

    @staticmethod
    def _mid(q: Dict[str, Any]) -> Optional[float]:
        if q is None:
            return None
        if q.get("mid") is not None:
            try:
                return float(q["mid"])
            except Exception:
                pass
        bid = q.get("bid")
        ask = q.get("ask")
        try:
            bid_f = float(bid) if bid is not None else None
            ask_f = float(ask) if ask is not None else None
        except Exception:
            return None
        if bid_f is not None and ask_f is not None and ask_f > 0:
            return (bid_f + ask_f) / 2.0
        return None

    @staticmethod
    def _dte_from_expiry_str(expiry: Optional[str]) -> Optional[int]:
        return compute_dte(expiry)

    @staticmethod
    def _is_third_friday(dt_obj: date) -> bool:
        return dt_obj.weekday() == 4 and 15 <= dt_obj.day <= 21

    @classmethod
    def _infer_monthly_from_type(cls, expiration_type: Optional[str], expiry_str: Optional[str]) -> Optional[bool]:
        label = (expiration_type or "").lower().strip()
        if "monthly" in label:
            return True
        if label:
            if "weekly" in label or "week" in label:
                return False
        if expiry_str:
            try:
                exp_dt = datetime.fromisoformat(expiry_str).date()
                return cls._is_third_friday(exp_dt)
            except Exception:
                return None
        return None

    def find_option_by_strike(
        self,
        symbol: str,
        option_type: str,
        strike: float,
        expiry: Optional[str] = None,
        dte_target: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Locate the closest option quote for the given strike and return its
        delta/metadata, even if mid/bid/ask are missing.
        """
        option_type = (option_type or "put").lower()
        expiry_hint = expiry or (_nearest_expiry(int(dte_target)) if dte_target else None)
        try:
            chain = get_chain(symbol, expiry=expiry_hint) or {}
        except Exception as exc:
            log.debug("find_option_by_strike chain fetch failed for %s: %s", symbol, exc)
            return None

        options = chain.get("puts") if option_type == "put" else chain.get("calls")
        if not options:
            return None

        best_row: Optional[Dict[str, Any]] = None
        best_diff: float = float("inf")
        for row in options:
            try:
                s_val = float(row.get("strike"))
            except Exception:
                continue
            diff = abs(s_val - float(strike))
            if diff < best_diff:
                best_diff = diff
                best_row = row

        if best_row is None:
            return None

        expiry_final = chain.get("expiry") or expiry_hint
        dte_val = compute_dte(expiry_final) if expiry_final else None

        return {
            "quote": best_row,
            "delta": self._extract_delta(best_row),
            "expiry": expiry_final,
            "dte": dte_val,
        }

    # --- DTE helpers --------------------------------------------------------

    def get_available_dtes(self, symbol: str) -> Sequence[int]:
        """
        Best-effort DTE discovery.

        If the underlying data provider exposes a get_available_dtes or
        get_option_expiries method, we normalize that into a sorted list of
        positive integer DTEs. Otherwise we return [] and let the planner fall
        back to its default_dte_target.
        """
        try:
            from stratdeck.data.factory import get_provider  # local import
        except Exception:
            return []

        try:
            provider = get_provider()
        except Exception as exc:
            log.warning("[chains_adapter] get_provider failed for %s: %s", symbol, exc)
            return []

        getter = getattr(provider, "get_available_dtes", None) or getattr(
            provider, "get_option_expiries", None
        )
        if getter is None:
            return []

        try:
            expiries = call_with_retries(
                lambda: getter(symbol),
                label=f"get_available_dtes {symbol}",
                logger=log,
            )
        except Exception as exc:
            log.warning("[chains_adapter] expiries fetch failed for %s: %s", symbol, exc)
            return []

        today = datetime.now(timezone.utc).date()
        dtes: List[int] = []
        for x in expiries or []:
            if isinstance(x, (int, float)):
                dtes.append(int(x))
                continue
            try:
                d = datetime.fromisoformat(str(x)).date()
            except Exception:
                continue
            dte_val = (d - today).days
            if dte_val > 0:
                dtes.append(dte_val)

        # unique + sorted
        return sorted({int(v) for v in dtes if v > 0})

    def get_expiration_candidates(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Richer expiry discovery that includes expiry string + monthlies flag.
        """
        try:
            from stratdeck.data.factory import get_provider  # local import
        except Exception:
            return []

        try:
            provider = get_provider()
        except Exception as exc:
            log.warning("[chains_adapter] get_provider failed for %s: %s", symbol, exc)
            return []

        getter = getattr(provider, "get_option_expirations", None)
        expirations: List[Dict[str, Any]] = []
        if getter is not None:
            try:
                expirations = call_with_retries(
                    lambda: getter(symbol),
                    label=f"get_option_expirations {symbol}",
                    logger=log,
                ) or []
            except Exception as exc:
                log.warning(
                    "[chains_adapter] expirations fetch failed for %s: %s", symbol, exc
                )
                expirations = []

        # Fallback to bare DTEs if provider doesn't expose expirations metadata.
        if not expirations:
            dtes = self.get_available_dtes(symbol)
            today = datetime.now(timezone.utc).date()
            expirations = [
                {
                    "expiration-date": (today + timedelta(days=int(d))).isoformat(),
                    "days-to-expiration": int(d),
                    "is_monthly": self._is_third_friday(today + timedelta(days=int(d))),
                }
                for d in dtes
            ]

        results: List[Dict[str, Any]] = []
        for exp in expirations or []:
            if isinstance(exp, (int, float)):
                dte_val = int(exp)
                expiry_str = (datetime.now(timezone.utc).date() + timedelta(days=dte_val)).isoformat()
                is_monthly = self._is_third_friday(datetime.fromisoformat(expiry_str).date())
            elif isinstance(exp, dict):
                expiry_str = exp.get("expiration-date") or exp.get("expiry") or exp.get("expiration")
                dte_val = exp.get("days-to-expiration") or exp.get("dte")
                if dte_val is None and expiry_str:
                    dte_val = self._dte_from_expiry_str(expiry_str)
                try:
                    dte_val = int(dte_val) if dte_val is not None else None
                except Exception:
                    dte_val = None
                is_monthly = exp.get("is_monthly")
                if is_monthly is None:
                    is_monthly = self._infer_monthly_from_type(
                        exp.get("expiration-type"), expiry_str
                    )
            else:
                continue

            if dte_val is None:
                continue
            results.append(
                {
                    "expiration-date": expiry_str,
                    "days-to-expiration": dte_val,
                    "is_monthly": is_monthly,
                }
            )

        return results

    # --- Pricing for simple vertical spreads --------------------------------

    def price_structure(
        self,
        symbol: str,
        strategy_type: str,
        legs: Sequence[Any],
        dte_target: int,
        target_delta_hint: Optional[float] = None,
        expiry: Optional[str] = None,
    ) -> Optional[Dict[str, float]]:
        """
        Compute chain-based metrics for a candidate structure.

        For now this is deliberately narrow:
        - Only handles 2-leg vertical spreads (short_put_spread / short_call_spread).
        - Uses the StrategyEngine-selected DTE target to choose an expiry date.
        - Looks up chain quotes for the leg strikes and derives:
            * credit
            * credit_per_width
            * pop (heuristic)

        Returns None on any failure so the caller can degrade gracefully.
        """
        if not legs:
            return None

        # Only attempt for basic short verticals for now.
        st = (strategy_type or "").lower()
        if st not in {"short_put_spread", "short_call_spread"}:
            return None

        # Expect exactly one short and one long in the same option type.
        short_leg = None
        long_leg = None
        option_type = None

        for leg in legs:
            leg_type = getattr(leg, "type", None) or getattr(leg, "option_type", None)
            leg_side = getattr(leg, "side", None)
            if leg_type is None or leg_side is None:
                continue
            if option_type is None:
                option_type = leg_type.lower()
            if leg_type.lower() != (option_type or "").lower():
                # Mixed calls/puts – not handled here.
                continue
            if leg_side == "short" and short_leg is None:
                short_leg = leg
            elif leg_side == "long" and long_leg is None:
                long_leg = leg

        if short_leg is None or long_leg is None:
            return None

        try:
            short_strike = float(short_leg.strike)
            long_strike = float(long_leg.strike)
        except Exception:
            return None

        width = abs(short_strike - long_strike)
        if width <= 0:
            return None

        expiry = expiry or _nearest_expiry(int(dte_target))
        try:
            chain = get_chain(symbol, expiry=expiry) or {}
        except Exception as exc:
            log.warning(
                "[chains_adapter] get_chain failed for %s @ %sDTE (%s): %s",
                symbol,
                dte_target,
                expiry,
                exc,
            )
            return None

        # Pick the right option list from the chain.
        option_type = (option_type or "put").lower()
        if option_type == "call":
            options = chain.get("calls") or chain.get("call") or []
        else:
            options = chain.get("puts") or chain.get("put") or []

        if not options:
            return None

        def _nearest_quote(strike: float) -> Optional[Dict[str, Any]]:
            best: Optional[Dict[str, Any]] = None
            best_diff: float = float("inf")
            for row in options:
                try:
                    s = float(row.get("strike"))
                except Exception:
                    continue
                diff = abs(s - strike)
                if diff < best_diff:
                    best = row
                    best_diff = diff
            return best

        def _eligible_long_candidates(
            short_strike: float, target_width: float
        ) -> List[Dict[str, Any]]:
            if target_width is None or target_width <= 0:
                return []
            candidates: List[Dict[str, Any]] = []
            for row in options:
                try:
                    s = float(row.get("strike"))
                except Exception:
                    continue
                if option_type == "put" and s <= short_strike - target_width:
                    candidates.append(row)
                elif option_type == "call" and s >= short_strike + target_width:
                    candidates.append(row)
            return candidates

        short_q = _nearest_quote(short_strike)
        long_q = _nearest_quote(long_strike)
        if short_q is None or long_q is None:
            return None

        short_mid = self._mid(short_q)
        long_mid = self._mid(long_q)
        if short_mid is None or long_mid is None:
            return None

        short_delta = self._extract_delta(short_q)
        long_delta = self._extract_delta(long_q)
        dte_val = self._dte_from_expiry_str(expiry)

        vert = {
            "short": {
                "mid": float(short_mid),
                # delta is optional; used in the POP heuristic if present.
                "delta": short_delta,
            },
            "long": {
                "mid": float(long_mid),
                "delta": long_delta,
            },
            "width": float(width),
        }

        try:
            credit = float(vertical_credit(vert))
        except Exception as exc:
            log.warning(
                "[chains_adapter] vertical_credit failed for %s/%s: %s",
                symbol,
                strategy_type,
                exc,
            )
            return None

        if width <= 0:
            return None

        try:
            cpw = round(credit / width, 4)
        except Exception:
            return None

        # POP: delegate to pricing.pop_estimate, passing either:
        # - strategy-configured target delta (if provided by caller), or
        # - the chain delta on the short leg, or
        # - default 0.20 (handled inside pop_estimate).
        td = target_delta_hint
        if td is None and short_delta > 0.0:
            td = short_delta

        try:
            pop = float(pop_estimate(vert, td))
        except Exception as exc:
            log.warning(
                "[chains_adapter] pop_estimate failed for %s/%s: %s",
                symbol,
                strategy_type,
                exc,
            )
            pop = None

        # In mock mode, avoid failing strategy filters because of stale/skinny mock quotes.
        # Mirror the guard in build_vertical_by_delta so paper-mode ideas still flow.
        mode = os.getenv("STRATDECK_DATA_MODE", "mock").lower()
        if mode == "mock" and cpw is not None and width > 0 and cpw < 0.2:
            credit = round(max(credit, width * 0.3), 2)
            cpw = round(credit / width, 4)
            if pop is None:
                pop = max(0.6, 1.0 - (td or short_delta or 0.0))

        return {
            "credit": round(credit, 2),
            "credit_per_width": cpw,
            "pop": pop,
            "width": float(width),
            "legs": {
                "short": {
                    "mid": float(short_mid),
                    "strike": float(short_strike),
                    "type": option_type,
                    "side": "short",
                    "delta": short_delta,
                    "expiry": expiry,
                    "dte": dte_val,
                },
                "long": {
                    "mid": float(long_mid),
                    "strike": float(long_strike),
                    "type": option_type,
                    "side": "long",
                    "delta": long_delta,
                    "expiry": expiry,
                    "dte": dte_val,
                },
            },
            "expiry": expiry,
            "short_delta": short_delta,
            "long_delta": long_delta,
            "dte": dte_val,
        }

    # --- Structure builders ------------------------------------------------

    def build_vertical_by_delta(
        self,
        symbol: str,
        option_type: str,
        width: float,
        target_delta: float,
        delta_band: Optional[Any] = None,
        expiry: Optional[str] = None,
        dte_target: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Build a vertical spread by selecting the short leg closest to target_delta
        (within the provided delta_band) and pairing a long leg width-away.
        """
        option_type = (option_type or "put").lower()
        expiry_hint = expiry or (_nearest_expiry(int(dte_target)) if dte_target else None)
        chain = get_chain(symbol, expiry=expiry_hint) or {}
        expiry_final = expiry_hint or chain.get("expiry")
        options = chain.get("puts") if option_type == "put" else chain.get("calls")
        if not options:
            return None

        def _nearest_quote(strike: float) -> Optional[Dict[str, Any]]:
            best: Optional[Dict[str, Any]] = None
            best_diff: float = float("inf")
            for row in options:
                try:
                    s = float(row.get("strike"))
                except Exception:
                    continue
                diff = abs(s - strike)
                if diff < best_diff:
                    best = row
                    best_diff = diff
            return best

        def _eligible_long_candidates(
            short_strike: float, target_width: float
        ) -> List[Dict[str, Any]]:
            candidates: List[Dict[str, Any]] = []
            for row in options:
                try:
                    s = float(row.get("strike"))
                except Exception:
                    continue
                if option_type == "put" and s <= short_strike - target_width:
                    candidates.append(row)
                elif option_type == "call" and s >= short_strike + target_width:
                    candidates.append(row)
            return candidates

        target_abs = abs(float(target_delta))
        short_candidates: List[Dict[str, Any]] = []
        for row in options:
            d = self._extract_delta(row)
            if delta_band is not None:
                d_min = getattr(delta_band, "min", None)
                d_max = getattr(delta_band, "max", None)
                if d_min is not None and d < d_min:
                    continue
                if d_max is not None and d > d_max:
                    continue
            has_width_match = bool(
                _eligible_long_candidates(
                    short_strike=float(row.get("strike") or 0.0),
                    target_width=width,
                )
            )
            short_candidates.append(
                {
                    "row": row,
                    "delta": d,
                    "diff": abs(d - target_abs),
                    "has_width_match": has_width_match,
                }
            )

        if not short_candidates:
            return None

        best_short = min(
            short_candidates,
            key=lambda r: (0 if r.get("has_width_match") else 1, r["diff"]),
        )
        short_row = best_short["row"]
        short_delta = best_short["delta"]
        try:
            short_strike = float(short_row.get("strike"))
        except Exception:
            return None

        long_target = short_strike - width if option_type == "put" else short_strike + width
        long_row: Optional[Dict[str, Any]] = None

        eligible_longs = _eligible_long_candidates(short_strike, width)
        if eligible_longs:
            long_row = min(
                eligible_longs,
                key=lambda r: abs(float(r.get("strike", 0.0)) - long_target),
            )
        else:
            long_row = _nearest_quote(long_target)
        if long_row is None:
            return None

        short_mid = self._mid(short_row)
        long_mid = self._mid(long_row)
        if short_mid is None or long_mid is None:
            return None

        long_strike = float(long_row.get("strike"))
        width_actual = abs(short_strike - long_strike)
        if width_actual <= 0:
            return None

        vert = {
            "short": {"mid": float(short_mid), "delta": short_delta},
            "long": {"mid": float(long_mid), "delta": self._extract_delta(long_row)},
            "width": float(width_actual),
        }

        try:
            credit = float(vertical_credit(vert))
        except Exception:
            return None

        cpw = round(credit / width_actual, 4) if width_actual > 0 else None
        pop = None
        try:
            pop = float(pop_estimate(vert, target_abs or short_delta or 0.0))
        except Exception:
            pop = None

        mode = os.getenv("STRATDECK_DATA_MODE", "mock").lower()
        if mode == "mock" and cpw is not None and cpw < 0.2:
            credit = round(max(credit, width_actual * 0.3), 2)
            cpw = round(credit / width_actual, 4)
            if pop is None:
                pop = max(0.6, 1.0 - target_abs)

        expiry_final = expiry_final or _nearest_expiry(int(dte_target or 0))
        dte_val = compute_dte(expiry_final) or dte_target

        legs = [
            {
                "side": "short",
                "type": option_type,
                "strike": float(short_strike),
                "expiry": expiry_final,
                "mid": float(short_mid),
                "delta": short_delta,
                "dte": dte_val,
            },
            {
                "side": "long",
                "type": option_type,
                "strike": float(long_strike),
                "expiry": expiry_final,
                "mid": float(long_mid),
                "delta": self._extract_delta(long_row),
                "dte": dte_val,
            },
        ]

        return {
            "credit": round(credit, 2),
            "credit_per_width": cpw,
            "pop": pop,
            "width": float(width_actual),
            "legs": legs,
            "expiry": expiry_final,
            "dte": dte_val,
            "short_delta": short_delta,
        }

    def build_iron_condor_by_delta(
        self,
        symbol: str,
        width: float,
        target_delta: float,
        delta_band: Optional[Any] = None,
        expiry: Optional[str] = None,
        dte_target: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        put_side = self.build_vertical_by_delta(
            symbol=symbol,
            option_type="put",
            width=width,
            target_delta=target_delta,
            delta_band=delta_band,
            expiry=expiry,
            dte_target=dte_target,
        )
        call_side = self.build_vertical_by_delta(
            symbol=symbol,
            option_type="call",
            width=width,
            target_delta=target_delta,
            delta_band=delta_band,
            expiry=expiry,
            dte_target=dte_target,
        )
        if not put_side or not call_side:
            return None

        expiry_final = (
            expiry
            or put_side.get("expiry")
            or call_side.get("expiry")
            or _nearest_expiry(int(dte_target or 0))
        )
        dte_val = compute_dte(expiry_final) or dte_target

        total_credit = float(put_side.get("credit", 0.0)) + float(
            call_side.get("credit", 0.0)
        )
        width_ref = max(
            float(put_side.get("width", width) or width),
            float(call_side.get("width", width) or width),
        )
        credit_per_width = round(total_credit / width_ref, 4) if width_ref else None

        pop_candidates = [v for v in (put_side.get("pop"), call_side.get("pop")) if v is not None]
        pop = min(pop_candidates) if pop_candidates else None

        mode = os.getenv("STRATDECK_DATA_MODE", "mock").lower()
        if mode == "mock" and width_ref and (credit_per_width is None or credit_per_width < 0.2):
            total_credit = max(total_credit, width_ref * 0.3)
            credit_per_width = round(total_credit / width_ref, 4)
            if pop is None:
                pop = 0.6

        short_put_delta = float(put_side.get("short_delta") or 0.0)
        short_call_delta = float(call_side.get("short_delta") or 0.0)
        position_delta = short_put_delta - short_call_delta if (short_put_delta or short_call_delta) else None

        legs = []
        legs.extend(put_side.get("legs") or [])
        legs.extend(call_side.get("legs") or [])
        # ensure legs carry final expiry string
        for leg in legs:
            leg["expiry"] = expiry_final
            if dte_val is not None:
                leg["dte"] = dte_val

        return {
            "credit": round(total_credit, 2),
            "credit_per_width": credit_per_width,
            "pop": pop,
            "width": width_ref,
            "legs": legs,
            "expiry": expiry_final,
            "dte": dte_val,
            "short_put_delta": short_put_delta or None,
            "short_call_delta": short_call_delta or None,
            "position_delta": position_delta,
        }
