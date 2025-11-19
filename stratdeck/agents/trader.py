from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from ..agents.compliance import ComplianceAgent
from ..agents.journal import JournalAgent
from ..core.config import cfg
from ..core.policies import ComplianceResult
from ..tools.chains import fetch_vertical_candidates
from ..tools.orders import OrderLeg, OrderPlan, OrderPreview, place, preview, to_order
from ..tools.account import is_live_mode
from ..data.factory import get_provider
from ..tools.positions import add_position
from ..tools.pricing import credit_for_vertical, pop_estimate

DEBUG_TRADER_RANKING = os.getenv("STRATDECK_DEBUG_TRADER_RANKING") == "1"

# POP / credit-per-width based ranking defaults.
# Tune these once you see real chain-based metrics in your JSON.
MIN_POP = 0.55
MIN_CREDIT_PER_WIDTH = 0.15
MAX_CREDIT_PER_WIDTH = 0.45
TARGET_DTE = 45
MAX_DTE_DEVIATION = 10

POP_WEIGHT = 0.6
CPW_WEIGHT = 0.4
DTE_WEIGHT = 0.2


class TraderAgent:
    def __init__(self, compliance: Optional[ComplianceAgent] = None):
        self.compliance = compliance or ComplianceAgent.from_config(cfg())
        self.journal = JournalAgent()
        self.provider = get_provider() if is_live_mode() else None
        self.logger = logging.getLogger(__name__)

    def _resolve_expiry(self, leg: Dict[str, Any]) -> Optional[str]:
        for key in ("expiry", "exp", "expiration", "expiration_date"):
            if key in leg and leg[key]:
                return leg[key]
        return None

    def _build_spread_plan(self, symbol: str, strategy: str, dte: int, width: int, target_delta: float) -> dict:
        candidates = fetch_vertical_candidates(symbol, dte, target_delta, width)
        short = candidates.get("short", {})
        long = candidates.get("long", {})
        expiry = (
            self._resolve_expiry(short)
            or self._resolve_expiry(long)
            or candidates.get("expiry")
            or ""
        )
        if not expiry:
            raise ValueError(f"Spread candidate missing expiry for {symbol}: {short}")

        legs = [
            {"side": "short", "type": short.get("type", "put"), "strike": short["strike"], "expiry": expiry},
            {"side": "long", "type": long.get("type", "put"), "strike": long["strike"], "expiry": expiry},
        ]

        return {
            "symbol": symbol,
            "strategy": strategy,
            "target_dte": dte,
            "width": candidates.get("width", width),
            "target_delta": target_delta,
            "credit": credit_for_vertical(candidates),
            "pop": pop_estimate(candidates, target_delta),
            "spread_plan_source": candidates,
            "expiry": expiry,
            "legs": legs,
        }

    def _order_plan_from_spread(self, spread_plan: dict, qty: int) -> OrderPlan:
        legs: List[OrderLeg] = []
        symbol = spread_plan["symbol"]
        for leg in spread_plan.get("legs", []):
            option_type = "C" if str(leg.get("type", "put")).lower().startswith("c") else "P"
            legs.append(
                OrderLeg(
                    symbol=symbol,
                    expiry=leg["expiry"],
                    strike=float(leg["strike"]),
                    option_type=option_type,
                    side=leg["side"].upper(),
                    qty=qty,
                    price=float(leg.get("mid") or leg.get("price") or 0.0),
                )
            )

        strategy = spread_plan.get("strategy", "PUT_CREDIT").upper()
        return OrderPlan(
            strategy=f"{strategy}_SPREAD" if "SPREAD" not in strategy else strategy,
            underlying=symbol,
            is_index=bool(
                spread_plan.get("is_index")
                or symbol.upper() in {"SPX", "XSP", "RUT", "NDX"}
            ),
            legs=legs,
            spread_width=float(spread_plan.get("width", 0.0)),
            credit_per_spread=float(spread_plan.get("credit", 0.0)),
            qty=qty,
            notes=spread_plan.get("note") or spread_plan.get("rationale"),
        )

    def build_order_plan(self, spread_plan: dict, qty: int) -> tuple[OrderPlan, OrderPreview, dict]:
        order_plan = self._order_plan_from_spread(spread_plan, qty)
        pv = preview(order_plan, fee_per_contract_leg=self.compliance.pack.fee_per_contract_leg)
        summary = {
            "spread_plan": spread_plan,
            "qty": int(qty),
            "tif": "DAY",
            "price": round(float(spread_plan.get("credit", 0.0)), 2),
            "est_bp_impact": pv.bp_required,
            "fees": pv.est_fees,
            "max_loss": pv.max_loss,
            "preview": {
                "total_credit": pv.total_credit,
                "est_fees": pv.est_fees,
                "max_loss": pv.max_loss,
                "bp_required": pv.bp_required,
            },
        }
        return order_plan, pv, summary

    def _format_compliance(self, result: ComplianceResult) -> Dict[str, Any]:
        return {
            "allowed": result.ok,
            "summary": result.summary(),
            "reasons": [f"[{v.code}] {v.message}" for v in result.violations],
        }

    def enter_trade(self, spread_plan: dict, qty: int, portfolio: Optional[dict] = None,
                    confirm: bool = False, live_order: bool = False) -> dict:
        order_plan, pv, summary = self.build_order_plan(spread_plan, qty)
        comp_result = self.compliance.approve(
            plan=order_plan,
            preview=pv,
            candidate=spread_plan,
        )
        compliance_summary = self._format_compliance(comp_result)

        if not compliance_summary["allowed"]:
            return {
                "compliance": compliance_summary,
                "order_plan": summary,
            }

        fill = None

        if confirm:
            if live_order and self.provider:
                tasty_order = self._to_tasty_order(order_plan, summary["price"])
                try:
                    fill = self.provider.place_order(tasty_order)
                except Exception as exc:
                    self.logger.warning(
                        "[orders] live place failed (%s); falling back to paper fill",
                        exc,
                    )
            if fill is None:
                fill = place(spread_plan, qty)

            if fill:
                out_fill = {
                    "status": fill.get("status") if isinstance(fill, dict) else None,
                    "position_id": fill.get("position_id") if isinstance(fill, dict) else getattr(fill, "position_id", None),
                    "details": fill,
                }
            else:
                out_fill = None

            pos = add_position(spread_plan, qty)
            position_id = pos.get("id") or (out_fill["position_id"] if out_fill else None)
            self.journal.log_open(position_id, spread_plan, qty, summary.get("preview", {}))
        else:
            out_fill = None

        result = {
            "compliance": compliance_summary,
            "order_plan": summary,
        }

        if out_fill:
            result["fill"] = out_fill
            if out_fill.get("position_id"):
                result["position_id"] = out_fill["position_id"]

        return result

    def vet_idea(
        self,
        idea: Any,
        qty: int = 1,
    ) -> dict:
        """
        Dry-run a TradeIdea through the full build_order_plan + ComplianceAgent
        without placing anything.

        Returns a small report dict with:
          - allowed: bool
          - violations: list[str]
          - spread_plan: dict
          - order_summary: dict (price, bp, etc.)
        """
        spread_plan = self.plan_from_idea(idea)
        order_plan, pv, summary = self.build_order_plan(spread_plan, qty)

        comp_result = self.compliance.approve(
            plan=order_plan,
            preview=pv,
            candidate=spread_plan,
        )
        compliance_summary = self._format_compliance(comp_result)

        return {
            "allowed": compliance_summary["allowed"],
            "violations": compliance_summary.get("reasons", []),
            "spread_plan": spread_plan,
            "order_summary": summary,
        }

    def enter_from_idea(
        self,
        idea: Any,
        qty: int = 1,
        *,
        confirm: bool = False,
        live_order: bool = False,
        portfolio: Optional[dict] = None,
    ) -> dict:
        """
        Convenience wrapper:
        - adapt TradeIdea -> spread_plan
        - build/preview OrderPlan
        - run compliance
        - optionally place (paper or live)
        """
        spread_plan = self.plan_from_idea(idea)
        return self.enter_trade(
            spread_plan=spread_plan,
            qty=qty,
            portfolio=portfolio,
            confirm=confirm,
            live_order=live_order,
        )

    def plan_from_symbol(self, symbol: str, width: int, dte: int, target_delta: float = 0.20) -> dict:
        return self._build_spread_plan(symbol, "PUT_CREDIT", dte, width, target_delta)

    ALLOWED_INDEX_WIDTHS = (5, 10, 25)
    ALLOWED_EQUITY_WIDTHS = (1, 2, 3, 5)

    def plan_from_idea(
        self,
        idea: Any,
        *,
        default_target_dte: int = 45,
        default_spx_width: int = 5,
        default_xsp_width: int = 1,
        default_target_delta: float = 0.20,
    ) -> dict:
        if hasattr(idea, "to_dict"):
            data = idea.to_dict()
        elif isinstance(idea, dict):
            data = idea
        else:
            data = getattr(idea, "__dict__", {})

        def _get(*keys, default=None):
            for k in keys:
                if isinstance(data, dict) and k in data and data[k] is not None:
                    return data[k]
                if hasattr(idea, k):
                    v = getattr(idea, k)
                    if v is not None:
                        return v
            return default

        symbol = _get("trade_symbol", "symbol", "underlying")
        if not symbol:
            raise ValueError("TradeIdea has no symbol/trade_symbol/underlying set")
        symbol = str(symbol).upper()

        raw_strategy = (_get("strategy", "kind", default="short_put_spread") or "").lower()
        if "put" in raw_strategy:
            strategy = "PUT_CREDIT"
        elif "call" in raw_strategy:
            strategy = "CALL_CREDIT"
        else:
            strategy = "PUT_CREDIT"

        target_dte = int(_get("target_dte", "dte", default=default_target_dte))

        width_hint = _get("spread_width", "width")

        if symbol in {"SPX", "XSP"}:
            if width_hint is None:
                width = default_spx_width if symbol == "SPX" else default_xsp_width
            else:
                allowed = self.ALLOWED_INDEX_WIDTHS
                width = min(allowed, key=lambda w: abs(float(width_hint) - w))
        else:
            if width_hint is None:
                width = 3
            else:
                allowed = self.ALLOWED_EQUITY_WIDTHS
                width = min(allowed, key=lambda w: abs(float(width_hint) - w))
        width = int(width)

        target_delta = float(
            _get("target_delta", "delta", "entry_delta", default=default_target_delta)
        )

        spread_plan = self._build_spread_plan(
            symbol=symbol,
            strategy=strategy,
            dte=target_dte,
            width=width,
            target_delta=target_delta,
        )

        spread_plan["idea_id"] = _get("id", "idea_id")
        spread_plan["rationale"] = _get("rationale", "notes", "reason")
        spread_plan["source"] = _get("source", default="scanner")

        return spread_plan


    # --- POP / credit_per_width ranking helpers for TradeIdeas ---

        # --- POP / credit_per_width ranking helpers for TradeIdeas ---

    def _idea_metric(self, idea: Any, key: str) -> Optional[float]:
        """Best-effort extraction of a numeric metric from a TradeIdea or dict."""
        if hasattr(idea, key):
            val = getattr(idea, key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        if isinstance(idea, dict) and key in idea and idea[key] is not None:
            try:
                return float(idea[key])
            except (TypeError, ValueError):
                return None
        return None

    def _compute_tasty_score(
        self,
        idea: Any,
        cpw_min: float,
        cpw_max: float,
    ) -> Optional[float]:
        """Composite score based on POP, credit_per_width, and DTE proximity.

        Returns None if the idea fails hard filters.
        """
        pop = self._idea_metric(idea, "pop")
        cpw = self._idea_metric(idea, "credit_per_width")
        dte_target = self._idea_metric(idea, "dte_target")

        if pop is None or cpw is None:
            if DEBUG_TRADER_RANKING:
                self.logger.debug("skip idea %r: missing pop/credit_per_width", idea)
            return None

        # ---- HARD FILTERS ----
        # Only POP is a hard gate for now.
        if pop < MIN_POP:
            if DEBUG_TRADER_RANKING:
                self.logger.debug("skip idea %r: POP %.3f < %.3f", idea, pop, MIN_POP)
            return None

        # ---- POP score: map [0.50, 0.70] -> [0, 1] (clamped) ----
        pop_score = (pop - 0.50) / 0.20
        pop_score = max(0.0, min(pop_score, 1.0))

        # ---- CPW score: dynamic scaling based on this batch of ideas ----
        if cpw_max > cpw_min and cpw > 0:
            cpw_score = (cpw - cpw_min) / (cpw_max - cpw_min)
            cpw_score = max(0.0, min(cpw_score, 1.0))
        else:
            # If all cpw are equal or we couldn't get a sensible range,
            # treat them as neutral and let POP / DTE dominate.
            cpw_score = 0.5

        # ---- DTE score: how close to TARGET_DTE ----
        if dte_target is not None:
            delta_dte = abs(dte_target - TARGET_DTE)
            if delta_dte >= MAX_DTE_DEVIATION:
                dte_score = 0.0
            else:
                dte_score = 1.0 - (delta_dte / MAX_DTE_DEVIATION)
        else:
            dte_score = 0.0

        score = (
            POP_WEIGHT * pop_score +
            CPW_WEIGHT * cpw_score +
            DTE_WEIGHT * dte_score
        )

        if DEBUG_TRADER_RANKING:
            self.logger.debug(
                "idea %r -> score=%.4f (pop=%.3f, cpw=%.6f, cpw_range=[%.6f, %.6f], dte=%s)",
                idea,
                score,
                pop,
                cpw,
                cpw_min,
                cpw_max,
                dte_target,
            )

        return score

    def rank_trade_ideas(self, ideas: List[Any]) -> List[tuple[Any, float]]:
        """Return list of (idea, score) sorted best-first using tasty-style metrics."""

        # First pass: find CPW range for ideas that at least pass POP.
        cpw_values: List[float] = []
        for idea in ideas:
            pop = self._idea_metric(idea, "pop")
            cpw = self._idea_metric(idea, "credit_per_width")

            if pop is None or cpw is None:
                continue
            if pop < MIN_POP:
                continue
            if cpw > 0:
                cpw_values.append(cpw)

        if cpw_values:
            cpw_min = min(cpw_values)
            cpw_max = max(cpw_values)
        else:
            cpw_min = 0.0
            cpw_max = 0.0

        if DEBUG_TRADER_RANKING:
            self.logger.debug(
                "ranking %d ideas; POP gate=%.2f; cpw_range=[%.6f, %.6f]",
                len(ideas),
                MIN_POP,
                cpw_min,
                cpw_max,
            )

        # Second pass: compute scores.
        ranked: List[tuple[Any, float]] = []
        for idea in ideas:
            score = self._compute_tasty_score(idea, cpw_min, cpw_max)
            if score is None:
                continue
            ranked.append((idea, score))

        ranked.sort(key=lambda pair: pair[1], reverse=True)
        return ranked

    def pick_best_trade_idea(self, ideas: List[Any]) -> Any:
        """Select the top-ranked idea or raise if none survive filters."""
        ranked = self.rank_trade_ideas(ideas)
        if not ranked:
            raise ValueError("No trade ideas passed POP filters.")

        best_idea, best_score = ranked[0]
        if DEBUG_TRADER_RANKING:
            self.logger.info("picked idea %r with score %.4f", best_idea, best_score)
        return best_idea



    def _to_tasty_order(self, order_plan: OrderPlan, price: float) -> Dict[str, Any]:
        order = {
            "symbol": order_plan.underlying,
            "price": price,
            "time_in_force": "DAY",
            "legs": [
                {
                    "kind": "option",
                    "side": leg.side.lower(),
                    "qty": leg.qty,
                    "type": "call" if leg.option_type.upper() == "C" else "put",
                    "strike": leg.strike,
                    "expiry": leg.expiry,
                }
                for leg in order_plan.legs
            ],
        }
        return order
