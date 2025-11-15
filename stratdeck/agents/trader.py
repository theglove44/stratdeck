from __future__ import annotations

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


class TraderAgent:
    def __init__(self, compliance: Optional[ComplianceAgent] = None):
        self.compliance = compliance or ComplianceAgent.from_config(cfg())
        self.journal = JournalAgent()
        self.provider = get_provider() if is_live_mode() else None

    def _build_spread_plan(self, symbol: str, strategy: str, dte: int, width: int, target_delta: float) -> dict:
        vert = fetch_vertical_candidates(symbol, dte, target_delta, width)
        credit = credit_for_vertical(vert)
        pop = pop_estimate(vert, target_delta)
        plan = {
            "symbol": symbol,
            "strategy": strategy,
            "expiry": vert["expiry"],
            "width": vert["width"],
            "credit": credit,
            "pop": pop,
            "legs": {
                "short_put": vert["short"],
                "long_put": vert["long"]
            }
        }
        return plan

    def _order_plan_from_spread(self, spread_plan: dict, qty: int) -> OrderPlan:
        symbol = spread_plan["symbol"]
        expiry = spread_plan.get("expiry")
        legs = spread_plan.get("legs", {})
        short_leg = legs.get("short_put") or legs.get("short")
        long_leg = legs.get("long_put") or legs.get("long")

        def _make_leg(leg_data: dict, side: str) -> Optional[OrderLeg]:
            if not leg_data:
                return None
            strike = float(leg_data.get("strike", 0.0))
            price = float(leg_data.get("mid") or leg_data.get("price") or 0.0)
            option_type = leg_data.get("type", "put").upper()[0]
            return OrderLeg(
                symbol=symbol,
                expiry=expiry or leg_data.get("expiry", ""),
                strike=strike,
                option_type=option_type,
                side=side,
                qty=qty,
                price=price,
            )

        legs_list: List[OrderLeg] = [
            leg for leg in (_make_leg(short_leg, "SELL"), _make_leg(long_leg, "BUY")) if leg
        ]
        is_index = bool(spread_plan.get("is_index") or symbol.upper() in {"SPX", "XSP", "RUT", "NDX"})
        strategy = spread_plan.get("strategy", "PUT_CREDIT").upper()
        width = float(spread_plan.get("width", 0.0))
        credit = float(spread_plan.get("credit", 0.0))
        notes = spread_plan.get("note") or spread_plan.get("rationale")
        return OrderPlan(
            strategy=f"{strategy}_SPREAD" if "SPREAD" not in strategy else strategy,
            underlying=symbol,
            is_index=is_index,
            legs=legs_list,
            spread_width=width,
            credit_per_spread=credit,
            qty=qty,
            notes=notes,
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
        comp_result = self.compliance.approve(plan=order_plan, preview=pv, candidate=spread_plan)
        compliance_summary = self._format_compliance(comp_result)
        out = {"compliance": compliance_summary, "order_plan": summary}
        if live_order and self.provider and compliance_summary["allowed"]:
            tasty_order = self._to_tasty_order(order_plan, summary["price"])
            try:
                preview_resp = self.provider.preview_order(tasty_order)
                out["broker_preview"] = preview_resp
                if confirm:
                    placed = self.provider.place_order(tasty_order)
                    out["broker_order"] = placed
            except Exception as exc:
                out["broker_error"] = str(exc)
        if compliance_summary["allowed"] and confirm:
            fill = place(spread_plan, qty)
            out["fill"] = fill
            pos = add_position(spread_plan, qty)
            position_id = pos.get("id") or fill.get("position_id")
            out["position_id"] = position_id
            self.journal.log_open(position_id, spread_plan, qty, summary.get("preview", {}))
        return out

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

    def plan_from_idea(
        self,
        idea: Any,
        *,
        default_target_dte: int = 45,
        default_spx_width: int = 5,
        default_xsp_width: int = 1,
        default_target_delta: float = 0.20,
    ) -> dict:
        """
        Adapt a TradeIdea (from ta.py) into the spread_plan dict that the existing
        TraderAgent/OrderPlan pipeline understands.

        This is intentionally defensive: it works with either a dataclass-like
        object or a plain dict.
        """
        if hasattr(idea, "to_dict"):
            data = idea.to_dict()
        elif isinstance(idea, dict):
            data = idea
        else:
            # Fall back to __dict__ for dataclasses / simple objects
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

        # Resolve trade vs data symbol; ta.py should ensure these are set.
        symbol = _get("trade_symbol", "symbol", "underlying")
        if not symbol:
            raise ValueError("TradeIdea has no symbol/trade_symbol/underlying set")

        symbol = str(symbol).upper()

        # Strategy / side
        raw_strategy = (_get("strategy", "kind", default="PUT_CREDIT") or "PUT_CREDIT").upper()

        # Normalise a few common labels
        if raw_strategy in {"SHORT_PUT_VERTICAL", "SHORT_PUT_SPREAD"}:
            strategy = "PUT_CREDIT"
        elif raw_strategy in {"SHORT_CALL_VERTICAL", "SHORT_CALL_SPREAD"}:
            strategy = "CALL_CREDIT"
        else:
            strategy = raw_strategy

        # DTE / width / target delta
        target_dte = int(_get("target_dte", "dte", default=default_target_dte))
        width = _get("spread_width", "width")
        if width is None:
            if symbol == "SPX":
                width = default_spx_width
            elif symbol == "XSP":
                width = default_xsp_width
            else:
                # Fallback – you can tune this per underlying if you want
                width = default_xsp_width

        width = int(width)
        target_delta = float(_get("target_delta", "delta", "entry_delta", default=default_target_delta))

        # Optional metadata
        rationale = _get("rationale", "notes", "reason")
        idea_id = _get("id", "idea_id")
        source = _get("source", default="scanner")

        # Delegate strike snapping + expiry choice to existing chains/pricing stack
        plan = self._build_spread_plan(
            symbol=symbol,
            strategy=strategy,
            dte=target_dte,
            width=width,
            target_delta=target_delta,
        )

        # Attach the idea metadata so journal/compliance can see it
        if idea_id is not None:
            plan["idea_id"] = idea_id
        if rationale:
            plan["rationale"] = rationale
        plan["source"] = source

        return plan


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
