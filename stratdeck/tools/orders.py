# tools/orders.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Union
import os
import uuid

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

