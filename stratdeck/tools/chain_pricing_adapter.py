# stratdeck/tools/chain_pricing_adapter.py

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence, List

from .chains import get_chain, _nearest_expiry
from .pricing import vertical_credit, pop_estimate
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

    # --- Pricing for simple vertical spreads --------------------------------

    def price_structure(
        self,
        symbol: str,
        strategy_type: str,
        legs: Sequence[Any],
        dte_target: int,
        target_delta_hint: Optional[float] = None,
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

        expiry = _nearest_expiry(int(dte_target))
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

        short_q = _nearest_quote(short_strike)
        long_q = _nearest_quote(long_strike)
        if short_q is None or long_q is None:
            return None

        short_mid = _mid(short_q)
        long_mid = _mid(long_q)
        if short_mid is None or long_mid is None:
            return None

        short_delta = _extract_delta(short_q)

        vert = {
            "short": {
                "mid": float(short_mid),
                # delta is optional; used in the POP heuristic if present.
                "delta": short_delta,
            },
            "long": {
                "mid": float(long_mid),
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
                },
                "long": {
                    "mid": float(long_mid),
                    "strike": float(long_strike),
                    "type": option_type,
                    "side": "long",
                },
            },
            "expiry": expiry,
        }
