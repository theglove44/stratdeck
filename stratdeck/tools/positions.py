from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

log = logging.getLogger(__name__)

DEFAULT_POSITIONS_PATH = Path(".stratdeck/positions.json")
POS_PATH: Path = DEFAULT_POSITIONS_PATH


def _calc_dte(expiry: Optional[str]) -> Optional[int]:
    if not expiry:
        return None
    try:
        d = datetime.fromisoformat(str(expiry)).date()
    except Exception:
        return None
    today = datetime.now(timezone.utc).date()
    return max((d - today).days, 0)


def _normalize_notes(notes: Any) -> Optional[str]:
    if notes is None:
        return None
    if isinstance(notes, list):
        return "; ".join(str(n) for n in notes)
    return str(notes)


def _normalize_leg(leg: Any) -> Dict[str, Any]:
    """
    Lightweight leg normalizer that mirrors orders._leg_to_dict to avoid a circular import.
    """
    if hasattr(leg, "to_dict"):
        data = leg.to_dict()
    elif isinstance(leg, dict):
        data = dict(leg)
    else:
        data = getattr(leg, "__dict__", {}) or {}

    side = str(data.get("side") or data.get("position") or "").lower() or None
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

    try:
        qty = int(data.get("quantity", data.get("qty", 1)))
    except Exception:
        qty = 1

    strike = data.get("strike")
    try:
        strike = float(strike)
    except Exception:
        strike = strike

    mid = data.get("mid") or data.get("price") or data.get("entry_mid")
    try:
        mid = float(mid) if mid is not None else None
    except Exception:
        mid = None

    expiry = data.get("expiry") or data.get("exp") or data.get("expiration")

    return {
        "side": side,
        "type": leg_type or None,
        "strike": strike,
        "expiry": expiry,
        "quantity": qty,
        "entry_mid": mid,
    }


class PaperPositionLeg(BaseModel):
    side: Optional[str] = None
    type: Optional[str] = None
    strike: Optional[float] = None
    expiry: Optional[str] = None
    quantity: int = 1
    entry_mid: Optional[float] = None

    model_config = ConfigDict(populate_by_name=True)


class PaperPosition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    symbol: str
    trade_symbol: Optional[str] = None
    strategy: Optional[str] = None
    strategy_id: Optional[str] = None
    universe_id: Optional[str] = None
    direction: Optional[str] = None
    legs: List[PaperPositionLeg] = Field(default_factory=list)
    qty: int = 1
    entry_mid: float
    entry_total: Optional[float] = None
    max_profit_total: Optional[float] = None
    max_loss_total: Optional[float] = None
    spread_width: Optional[float] = None
    dte: Optional[int] = None
    opened_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "open"
    closed_at: Optional[datetime] = None
    exit_mid: Optional[float] = None
    exit_reason: Optional[str] = None
    realized_pl_total: Optional[float] = None
    notes: Optional[str] = None
    provenance: Optional[Any] = None
    underlying_price_hint: Optional[float] = None
    target_delta: Optional[float] = None
    account_id: Optional[str] = None
    contract_multiplier: float = 100.0

    @field_validator("opened_at", "closed_at", mode="before")
    def _parse_dt(cls, value: Any) -> Any:  # noqa: B902
        if value is None or isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return value

    @field_validator("status", mode="before")
    def _normalize_status(cls, value: Any) -> str:  # noqa: B902
        if value is None:
            return "open"
        return str(value).lower()

    @model_validator(mode="after")
    def _compute_entry_total(self) -> "PaperPosition":  # noqa: B902
        if self.entry_total is None:
            try:
                self.entry_total = round(float(self.entry_mid) * int(self.qty) * float(self.contract_multiplier), 2)
            except Exception:
                self.entry_total = None
        return self


class PositionsStore:
    def __init__(self, path: Path | str = POS_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.positions: List[PaperPosition] = self._load()

    def _load(self) -> List[PaperPosition]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("[positions] failed to read %s: %s", self.path, exc)
            return []

        items = raw if isinstance(raw, list) else [raw]
        positions: List[PaperPosition] = []
        for item in items:
            try:
                positions.append(PaperPosition.model_validate(item))
            except ValidationError as exc:
                log.warning("[positions] skipping invalid entry: %s", exc)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("[positions] unexpected entry error: %s", exc)
        return positions

    def _persist(self) -> None:
        payload = [p.model_dump(mode="json") for p in self.positions]
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp_path.replace(self.path)

    def list_positions(self, status: Optional[str] = None) -> List[PaperPosition]:
        if status is None:
            return list(self.positions)
        status = status.lower()
        return [p for p in self.positions if (p.status or "").lower() == status]

    def get_open_positions(self) -> List[PaperPosition]:
        return self.list_positions(status="open")

    def add_position(self, position: PaperPosition) -> PaperPosition:
        self.positions.append(position)
        self._persist()
        return position

    def upsert(self, position: PaperPosition) -> PaperPosition:
        for idx, existing in enumerate(self.positions):
            if str(existing.id) == str(position.id):
                self.positions[idx] = position
                self._persist()
                return position
        self.positions.append(position)
        self._persist()
        return position

    def get(self, position_id: str) -> Optional[PaperPosition]:
        for pos in self.positions:
            if str(pos.id) == str(position_id):
                return pos
        return None

    def update_position(self, position: PaperPosition) -> PaperPosition:
        return self.upsert(position)


def _position_from_plan(spread_plan: Dict[str, Any], qty: int, entry_mid_price: Optional[float]) -> PaperPosition:
    symbol = spread_plan.get("symbol") or spread_plan.get("underlying")
    trade_symbol = spread_plan.get("trade_symbol") or symbol
    strategy = spread_plan.get("strategy") or spread_plan.get("strategy_type")
    direction = spread_plan.get("direction")
    strategy_id = spread_plan.get("strategy_id")
    universe_id = spread_plan.get("universe_id")
    spread_width = spread_plan.get("spread_width", spread_plan.get("width"))
    expiry = spread_plan.get("expiry")
    dte = spread_plan.get("dte")
    if dte is None:
        dte = _calc_dte(expiry)

    entry_mid = spread_plan.get("credit", 0.0) if entry_mid_price is None else entry_mid_price
    try:
        entry_mid = float(entry_mid)
    except Exception:
        entry_mid = 0.0

    notes_val = _normalize_notes(spread_plan.get("notes"))

    legs_raw = spread_plan.get("legs") or []
    legs = [PaperPositionLeg.model_validate(_normalize_leg(leg)) for leg in legs_raw] if legs_raw else []

    return PaperPosition(
        symbol=symbol,
        trade_symbol=trade_symbol,
        strategy=strategy,
        strategy_id=strategy_id,
        universe_id=universe_id,
        direction=direction,
        legs=legs,
        qty=int(qty),
        entry_mid=entry_mid,
        spread_width=spread_width if spread_width not in ("", None) else None,
        dte=dte if dte not in ("", None) else None,
        notes=notes_val,
        provenance=spread_plan.get("provenance"),
        underlying_price_hint=spread_plan.get("underlying_price_hint"),
        target_delta=spread_plan.get("target_delta"),
        account_id=spread_plan.get("account_id"),
    )


def _legacy_dict(position: PaperPosition) -> Dict[str, Any]:
    data = position.model_dump(mode="python")
    data.update(
        {
            "credit": position.entry_mid,
            "entry_mid_price": position.entry_mid,
            "width": position.spread_width,
            "qty": position.qty,
            "dte": position.dte,
            "status": (position.status or "open").lower(),
        }
    )
    return data


def add_position(
    spread_plan: Dict[str, Any],
    qty: int,
    *,
    entry_mid_price: Optional[float] = None,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    plan = dict(spread_plan)
    if account_id and "account_id" not in plan:
        plan["account_id"] = account_id
    position = _position_from_plan(plan, qty, entry_mid_price)
    store = PositionsStore(POS_PATH)
    store.add_position(position)
    return {"id": position.id, "position": position}


def list_positions(status: Optional[str] = None) -> List[Dict[str, Any]]:
    store = PositionsStore(POS_PATH)
    positions = store.list_positions(status=status)
    return [_legacy_dict(p) for p in positions]


def close_position(position_id: str, exit_credit: float, exit_reason: Optional[str] = None) -> Dict[str, Any]:
    store = PositionsStore(POS_PATH)
    target_id = str(position_id)
    position = store.get(target_id)
    if position is None:
        raise ValueError(f"Position {position_id} not found")
    if (position.status or "").lower() == "closed":
        raise ValueError(f"Position {position_id} already closed")

    try:
        exit_price = float(exit_credit)
    except Exception:
        exit_price = float(exit_credit or 0.0)

    pnl = (float(position.entry_mid) - exit_price) * int(position.qty) * float(position.contract_multiplier)

    if position.max_profit_total is None and position.entry_total is not None:
        try:
            position.max_profit_total = float(position.entry_total)
        except Exception:
            position.max_profit_total = None
    if position.max_loss_total is None and position.spread_width not in ("", None):
        try:
            width_total = float(position.spread_width) * float(position.contract_multiplier) * int(position.qty)
            position.max_loss_total = max(width_total - float(position.entry_mid) * float(position.contract_multiplier) * int(position.qty), 0.0)
        except Exception:
            position.max_loss_total = position.max_loss_total

    position.status = "closed"
    position.exit_mid = exit_price
    position.closed_at = datetime.now(timezone.utc)
    position.realized_pl_total = pnl
    position.exit_reason = exit_reason or position.exit_reason or "manual"

    store.upsert(position)

    return {
        "id": position.id,
        "symbol": position.symbol,
        "entry_credit": position.entry_mid,
        "exit_credit": exit_price,
        "qty": position.qty,
        "pnl": pnl,
        "exit_reason": position.exit_reason,
        "realized_pl_total": pnl,
    }
