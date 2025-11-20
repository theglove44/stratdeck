# tools/orders.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union
import os
import uuid
import json

from stratdeck.tools.chain_pricing_adapter import ChainPricingAdapter
from stratdeck.tools.positions import add_position

if TYPE_CHECKING:
    from stratdeck.agents.trade_planner import TradeIdea, TradeLeg

# Integration: provider for live mode
try:
    from stratdeck.data.factory import get_provider
except Exception:
    get_provider = None  # allow running this module standalone in tests

# ========= Dataclasses and models =========

@dataclass
class OrderLeg:
    symbol: str
    expiry: str  # YYYY-MM-DD
    strike: float
    option_type: str  # 'C' or 'P'
    side: str  # 'SELL' or 'BUY'
    qty: int
    price: float = 0.0  # mid/limit per contract leg (optional when normalizing)

@dataclass
class OrderPlan:
    strategy: str  # e.g., 'PUT_CREDIT_SPREAD' or 'CALL_CREDIT_SPREAD'
    underlying: str
    is_index: bool
    legs: List[OrderLeg]
    spread_width: float
    credit_per_spread: float
    qty: int
    notes: Optional[str] = None

@dataclass
class OrderPreview:
    total_credit: float
    est_fees: float
    max_loss: float
    bp_required: float

@dataclass
class SpreadPlan:
    symbol: str
    legs: List[OrderLeg]
    limit_price: Optional[float] = None   # per share
    fee_per_contract: float = 1.0
    width: Optional[float] = None         # for verticals only
    credit: Optional[float] = None        # per share credit

# ========= Trading mode helpers =========

def trading_mode() -> str:
    """
    Trading intent flag separate from data sourcing.
    Defaults to paper for safety; live wiring remains opt-in.
    """
    return os.getenv("STRATDECK_TRADING_MODE", "paper").lower()


def _calc_dte(expiry: Optional[str]) -> Optional[int]:
    if not expiry:
        return None
    try:
        d = datetime.fromisoformat(str(expiry)).date()
    except Exception:
        return None
    today = datetime.now(timezone.utc).date()
    return max((d - today).days, 0)


def _leg_to_dict(leg: Any) -> Dict[str, Any]:
    if hasattr(leg, "to_dict"):
        data = leg.to_dict()
    elif isinstance(leg, dict):
        data = dict(leg)
    else:
        data = getattr(leg, "__dict__", {}) or {}

    side = str(data.get("side") or data.get("position") or "").lower()
    leg_type = str(
        data.get("type")
        or data.get("option_type")
        or data.get("kind")
        or data.get("optionType")
        or ""
    ).lower()
    if leg_type in {"c", "call"}:
        leg_type = "call"
    elif leg_type in {"p", "put"}:
        leg_type = "put"

    qty = data.get("quantity", data.get("qty", 1))
    try:
        qty = int(qty)
    except Exception:
        qty = 1

    expiry = data.get("expiry") or data.get("exp") or data.get("expiration")
    strike = data.get("strike")
    try:
        strike = float(strike)
    except Exception:
        strike = strike

    mid = data.get("mid") or data.get("price")
    try:
        mid = float(mid) if mid is not None else None
    except Exception:
        mid = None

    return {
        "side": side,
        "type": leg_type,
        "strike": strike,
        "expiry": expiry,
        "quantity": qty,
        "mid": mid,
    }


def _provenance_snapshot(idea_like: Any) -> Optional[str]:
    data = {}
    if hasattr(idea_like, "to_dict"):
        try:
            data = idea_like.to_dict()
        except Exception:
            data = {}
    elif isinstance(idea_like, dict):
        data = idea_like
    else:
        data = getattr(idea_like, "__dict__", {}) or {}

    provenance = data.get("provenance")
    assignment = data.get("strategy_assignment")
    if provenance is None and isinstance(assignment, dict):
        try:
            provenance = json.dumps(assignment, sort_keys=True)
        except Exception:
            provenance = str(assignment)

    notes = data.get("notes") or getattr(idea_like, "notes", None) or []
    prov_notes = [
        n for n in notes if isinstance(n, str) and n.lower().startswith("[provenance]")
    ]
    if prov_notes:
        joined = "; ".join(prov_notes)
        provenance = f"{provenance}; {joined}" if provenance else joined
    return provenance


def _net_mid(legs: List[Dict[str, Any]]) -> Optional[float]:
    have_mid = False
    net = 0.0
    for leg in legs:
        mid = leg.get("mid")
        if mid is None:
            continue
        have_mid = True
        try:
            mid_val = float(mid)
        except Exception:
            continue
        qty = leg.get("quantity", leg.get("qty", 1)) or 1
        try:
            qty_val = abs(int(qty))
        except Exception:
            qty_val = 1
        side = str(leg.get("side") or "").lower()
        sign = -1
        if side in {"short", "sell", "sell_to_open", "sell-to-open"}:
            sign = 1
        net += sign * mid_val * qty_val
    if not have_mid:
        return None
    return round(net, 4)


def _legs_from_pricing(pricing: Dict[str, Any], fallback_legs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not pricing:
        return []
    legs_block = pricing.get("legs") or {}
    legs: List[Dict[str, Any]] = []
    for label in ("short", "long"):
        row = legs_block.get(label)
        if isinstance(row, dict):
            entry = dict(row)
            entry.setdefault("side", label)
            legs.append(entry)
    if legs:
        return legs
    return fallback_legs


def _pricing_legs(legs: List[Any]) -> List[Any]:
    parsed: List[Any] = []
    for leg in legs:
        if hasattr(leg, "type") and hasattr(leg, "side"):
            parsed.append(leg)
            continue
        data = _leg_to_dict(leg)
        parsed.append(
            SimpleNamespace(
                type=data.get("type"),
                side=data.get("side"),
                strike=data.get("strike"),
                expiry=data.get("expiry"),
            )
        )
    return parsed

# ========= Converters =========

def _leg_to_normalized(leg: OrderLeg) -> Dict[str, Union[str, int, float]]:
    # Map to the normalized dict that live adapters expect
    opt_type = "call" if leg.option_type.upper() == "C" else "put"
    side = "sell" if leg.side.upper() == "SELL" else "buy"
    return {
        "kind": "option",
        "side": side,
        "qty": int(leg.qty),
        "type": opt_type,
        "strike": float(leg.strike),
        "expiry": leg.expiry,
    }

def to_order(spread_plan: Union[SpreadPlan, Dict[str, Union[str, int, float, dict, list]]],
             qty: Optional[int] = None) -> Dict[str, Union[str, int, float, list]]:
    """
    Accept your current SpreadPlan (or dict) and return a normalized order dict.
    """
    if isinstance(spread_plan, SpreadPlan):
        legs = [ _leg_to_normalized(leg) for leg in spread_plan.legs ]
        # Scale all legs to the top-level qty if provided
        if qty is not None:
            for l in legs:
                l["qty"] = int(qty)
        return {
            "symbol": spread_plan.symbol,
            "price": spread_plan.limit_price,   # may be None for marketable preview
            "time_in_force": "DAY",
            "legs": legs,
        }

    # Dict fallback (your existing dict-shaped plans)
    symbol = str(spread_plan.get("symbol"))
    legs_in = spread_plan.get("legs")
    out_legs: List[Dict[str, Union[str, int, float]]] = []
    if isinstance(legs_in, list):
        for leg in legs_in:
            # accept either normalized or C/P, SELL/BUY shaped
            if "kind" in leg:
                norm = dict(leg)
            else:
                opt_type = "call" if str(leg.get("option_type","")).upper() == "C" else "put"
                side = "sell" if str(leg.get("side","")).upper() == "SELL" else "buy"
                norm = {
                    "kind": "option",
                    "side": side,
                    "qty": int(leg.get("qty", 1 if qty is None else qty)),
                    "type": opt_type,
                    "strike": float(leg.get("strike")),
                    "expiry": str(leg.get("expiry")),
                }
            if qty is not None:
                norm["qty"] = int(qty)
            out_legs.append(norm)
    elif isinstance(legs_in, dict):
        # single-leg dict
        out_legs.append(_leg_to_normalized(OrderLeg(
            symbol=symbol,
            expiry=legs_in["expiry"],
            strike=float(legs_in["strike"]),
            option_type=str(legs_in["option_type"]),
            side=str(legs_in["side"]),
            qty=int(qty or legs_in.get("qty", 1))
        )))
    else:
        out_legs = []

    return {
        "symbol": symbol,
        "price": spread_plan.get("limit_price"),
        "time_in_force": spread_plan.get("time_in_force", "DAY"),
        "legs": out_legs,
    }

# ========= Paper preview engine (your math kept) =========

def _paper_preview(spread_plan: Dict[str, Union[int, float, str, dict, list]]) -> Dict[str, float]:
    """
    Minimal paper preview for verticals.
    Expected keys:
      - credit (per share), width (points), legs (list/dict), fee_per_contract
    """
    credit = float(spread_plan.get("credit", 0.0) or 0.0)
    width = float(spread_plan.get("width", 0.0) or 0.0)
    fee_per_contract = float(spread_plan.get("fee_per_contract", 1.0) or 1.0)

    legs = spread_plan.get("legs", [])
    if isinstance(legs, dict):
        legs_count = 1
    elif isinstance(legs, list):
        legs_count = sum(int(abs(int(l.get("qty", 1)))) for l in legs)
    else:
        legs_count = 0

    # totals per contract set
    total_credit = credit * 100.0
    max_loss = max(0.0, (width * 100.0) - total_credit)  # vertical risk: width - credit, scaled
    total_fees = fee_per_contract * legs_count
    est_bp = max_loss  # simple margin proxy for defined risk

    return {
        "total_credit": round(total_credit, 2),
        "total_fees": round(total_fees, 2),
        "max_loss": round(max_loss, 2),
        "est_bp_impact": round(est_bp, 2),
    }

def preview_dict(spread_plan: Dict[str, Union[int, float, str, dict, list]], qty: int) -> Dict[str, float]:
    """Dict-based preview kept for back-compat paths."""
    mode = os.getenv("STRATDECK_DATA_MODE", "mock").lower()
    order = to_order(spread_plan, qty=qty)
    if mode == "live" and get_provider:
        try:
            return get_provider().preview_order(order)
        except NotImplementedError:
            pass
        except Exception as exc:
            print(f"[orders] warn: provider preview failed ({exc}); using paper")
    return _paper_preview(spread_plan)

def preview_from_dict(spread_plan: Dict[str, Union[int, float, str, dict]], qty: int) -> Dict[str, float]:
    return preview_dict(spread_plan, qty)

# Dataclass-based preview used by newer agents/compliance
FEE_PER_CONTRACT_LEG_DEFAULT = 1.25

def preview(plan: OrderPlan, fee_per_contract_leg: float = FEE_PER_CONTRACT_LEG_DEFAULT) -> OrderPreview:
    total_credit = plan.credit_per_spread * plan.qty * 100.0
    legs_per_spread = len(plan.legs)
    est_fees = fee_per_contract_leg * plan.qty * legs_per_spread
    max_loss_per_spread = max(plan.spread_width * 100.0 - plan.credit_per_spread * 100.0, 0.0)
    max_loss = max_loss_per_spread * plan.qty
    bp_required = max_loss
    return OrderPreview(
        total_credit=round(total_credit, 2),
        est_fees=round(est_fees, 2),
        max_loss=round(max_loss, 2),
        bp_required=round(bp_required, 2),
    )

def enter_paper_trade(
    trade_idea: "TradeIdea",
    qty: int = 1,
    *,
    account_id: Optional[str] = None,
    data_mode: Optional[str] = None,
    pricing_client: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Paper-only entry point for TradeIdeas.

    - Prices legs at mid using the chain pricing adapter.
    - Computes net credit/debit and credit_per_width.
    - Writes the fill to the positions store (paper ledger).
    """
    mode = trading_mode()
    if mode != "paper":
        raise ValueError("Live trading not implemented here. Set STRATDECK_TRADING_MODE=paper.")

    idea_dict = trade_idea.to_dict() if hasattr(trade_idea, "to_dict") else getattr(trade_idea, "__dict__", {}) or {}

    symbol = idea_dict.get("trade_symbol") or idea_dict.get("symbol")
    if not symbol:
        raise ValueError("Trade idea missing symbol/trade_symbol")
    symbol = str(symbol).upper()

    underlying = idea_dict.get("underlying") or symbol
    strategy = idea_dict.get("strategy") or idea_dict.get("strategy_type") or "unknown"
    direction = idea_dict.get("direction")
    spread_width = idea_dict.get("spread_width")

    raw_legs = getattr(trade_idea, "legs", None) or idea_dict.get("legs", []) or []
    legs = [_leg_to_dict(l) for l in raw_legs]
    expiry = None
    for leg in legs:
        if leg.get("expiry"):
            expiry = str(leg["expiry"])
            break
    dte = _calc_dte(expiry)
    target_dte = idea_dict.get("dte_target") or dte

    active_data_mode = (data_mode or os.getenv("STRATDECK_DATA_MODE", "mock")).lower()
    pricing_adapter = pricing_client or ChainPricingAdapter()
    pricing: Optional[Dict[str, Any]] = None
    if pricing_adapter and hasattr(pricing_adapter, "price_structure"):
        try:
            pricing = pricing_adapter.price_structure(
                symbol=symbol,
                strategy_type=strategy,
                legs=_pricing_legs(raw_legs),
                dte_target=int(target_dte or 0),
                target_delta_hint=idea_dict.get("target_delta"),
            )
        except Exception as exc:
            print(f"[orders] warn: price_structure failed for {symbol}: {exc}")
            pricing = None

    leg_quotes = _legs_from_pricing(pricing or {}, legs)
    if pricing and pricing.get("width") and spread_width is None:
        spread_width = pricing.get("width")

    net_mid = _net_mid(leg_quotes)
    credit_per_width = pricing.get("credit_per_width") if pricing else None
    if net_mid is None and pricing and pricing.get("credit") is not None:
        try:
            net_mid = float(pricing["credit"])
        except Exception:
            net_mid = None

    if net_mid is None:
        try:
            net_mid = float(idea_dict.get("estimated_credit"))
        except Exception:
            net_mid = 0.0

    net_mid = float(net_mid or 0.0)
    if credit_per_width is None and spread_width:
        try:
            credit_per_width = round(net_mid / float(spread_width), 4)
        except Exception:
            credit_per_width = None

    total_credit = round(net_mid * qty * 100.0, 2)
    provenance = _provenance_snapshot(idea_dict)

    pos = add_position(
        {
            "symbol": symbol,
            "underlying": underlying,
            "strategy": strategy,
            "direction": direction,
            "expiry": expiry or "",
            "width": spread_width or 0.0,
            "credit": net_mid,
            "dte": dte,
            "provenance": provenance,
            "notes": idea_dict.get("notes"),
            "target_delta": idea_dict.get("target_delta"),
            "account_id": account_id,
        },
        qty=qty,
        entry_mid_price=net_mid,
    )

    position_id = pos.get("id")
    return {
        "position_id": position_id,
        "symbol": symbol,
        "underlying": underlying,
        "strategy": strategy,
        "direction": direction,
        "qty": qty,
        "expiry": expiry,
        "dte": dte,
        "entry_mid_price": round(net_mid, 4),
        "total_credit": total_credit,
        "credit_per_width": credit_per_width,
        "legs": leg_quotes,
        "provenance": provenance,
        "trading_mode": mode,
        "data_mode": active_data_mode,
    }

# ========= Placement =========

def place(order_or_spread_plan: Union[Dict[str, Union[str, int, float, list, dict]], SpreadPlan],
          qty: Optional[int] = None) -> Dict[str, Union[str, int, float]]:
    """
    Public entry point:
      - live → provider.place_order(normalized)
      - mock → paper placement stub
    """
    mode = os.getenv("STRATDECK_DATA_MODE", "mock").lower()
    if isinstance(order_or_spread_plan, (dict, SpreadPlan)):
        order = to_order(order_or_spread_plan, qty=qty)
    else:
        raise TypeError("place() expects a SpreadPlan or dict")

    if mode == "live" and get_provider:
        try:
            return get_provider().place_order(order)
        except NotImplementedError:
            pass
        except Exception as exc:
            print(f"[orders] warn: provider place failed ({exc}); using paper fill")

    return place_paper(order)

def place_paper(order_dict: Dict[str, Union[str, int, float, list, dict]]) -> Dict[str, Union[str, int, float]]:
    # Keep your existing behavior, but accept normalized order dict
    return {
        "position_id": f"paper-{uuid.uuid4().hex[:8]}",
        "status": "SIMULATED",
        "symbol": order_dict.get("symbol"),
        "qty": sum(int(l.get("qty", 0)) for l in order_dict.get("legs", []) if isinstance(l, dict)) or 0,
    }
