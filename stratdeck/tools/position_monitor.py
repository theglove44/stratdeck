from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field

from stratdeck.data.factory import get_provider
from stratdeck.tools.chains import _nearest_expiry, get_chain
from stratdeck.tools.orders import _net_mid
from stratdeck.tools.positions import PaperPosition
from stratdeck.tools.pricing import last_price
from stratdeck.tools.vol import load_snapshot

log = logging.getLogger(__name__)


class PositionMetrics(BaseModel):
    position_id: str
    symbol: str
    trade_symbol: str
    strategy_id: str
    universe_id: Optional[str] = None

    underlying_price: float
    entry_mid: float
    current_mid: float
    unrealized_pl_per_contract: Optional[float] = None
    unrealized_pl_total: Optional[float] = None

    max_profit_per_contract: Optional[float] = None
    max_profit_total: Optional[float] = None
    max_loss_per_contract: Optional[float] = None
    max_loss_total: Optional[float] = None

    pnl_pct_of_max_profit: Optional[float] = None
    pnl_pct_of_max_loss: Optional[float] = None

    expiry: Optional[datetime] = None
    dte: Optional[float] = None
    as_of: datetime

    iv: Optional[float] = None
    ivr: Optional[float] = None

    is_short_premium: bool
    strategy_family: str


class ExitRulesConfig(BaseModel):
    strategy_family: str
    is_short_premium: bool
    profit_target_basis: str
    profit_target_pct: float
    dte_exit: int = 21
    ivr_soft_exit_below: Optional[float] = 20.0
    loss_management_style: Optional[str] = None


class ExitDecision(BaseModel):
    position_id: str
    action: str
    reason: str
    triggered_rules: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


_EXIT_RULES_CACHE: Dict[str, ExitRulesConfig] = {}
_EXITS_CONFIG_RAW: Optional[Dict[str, Any]] = None


def _load_exits_config() -> Dict[str, Any]:
    global _EXITS_CONFIG_RAW
    if _EXITS_CONFIG_RAW is not None:
        return _EXITS_CONFIG_RAW
    cfg_path = Path(__file__).resolve().parent.parent / "config" / "exits.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Exit rules config not found at {cfg_path}")
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise RuntimeError(f"Failed to load exit rules from {cfg_path}: {exc}") from exc
    _EXITS_CONFIG_RAW = raw
    return raw


def _merge_defaults(strategy_block: Dict[str, Any]) -> Dict[str, Any]:
    raw = _load_exits_config()
    defaults = raw.get("defaults", {}) or {}
    merged = dict(strategy_block)
    if merged.get("is_short_premium"):
        merged.setdefault("ivr_soft_exit_below", defaults.get("short_premium", {}).get("ivr_soft_exit_below"))
    return merged


def _fallback_family(strategy_id: str) -> Tuple[str, bool]:
    sid = (strategy_id or "").lower()
    if "strangle" in sid:
        return "short_strangle", True
    if "iron_condor" in sid:
        return "iron_condor", True
    if "credit_spread" in sid or "vertical" in sid or "spread" in sid:
        return "credit_spread", True
    if "ratio" in sid:
        return "ratio_spread", True
    if "bwb" in sid or "broken_wing_butterfly" in sid:
        return "bwb", True
    if "diagonal" in sid:
        return "diagonal", False
    return "unknown", True


def load_exit_rules(strategy_id: str) -> ExitRulesConfig:
    if strategy_id in _EXIT_RULES_CACHE:
        return _EXIT_RULES_CACHE[strategy_id]

    raw = _load_exits_config()
    strategies = raw.get("strategies", {}) or {}
    block = strategies.get(strategy_id)
    if block is None:
        family, short_prem = _fallback_family(strategy_id)
        cfg = ExitRulesConfig(
            strategy_family=family,
            is_short_premium=short_prem,
            profit_target_basis="credit",
            profit_target_pct=0.5,
            dte_exit=21,
            ivr_soft_exit_below=20.0 if short_prem else None,
        )
        _EXIT_RULES_CACHE[strategy_id] = cfg
        return cfg

    merged = _merge_defaults(block)
    cfg = ExitRulesConfig(**merged)
    _EXIT_RULES_CACHE[strategy_id] = cfg
    return cfg


def _to_percent(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        val = float(value)
    except Exception:
        return None
    return val * 100.0 if 0 <= val <= 1 else val


def _parse_expiry(position: PaperPosition) -> Tuple[Optional[str], Optional[datetime]]:
    expiry_str: Optional[str] = None
    if getattr(position, "expiry", None):
        expiry_str = str(position.expiry)
    if expiry_str is None:
        for leg in position.legs or []:
            if leg.expiry:
                expiry_str = str(leg.expiry)
                break
    if expiry_str is None and getattr(position, "provenance", None):
        expiry_str = getattr(position.provenance, "get", lambda *_: None)("expiry")
    expiry_dt: Optional[datetime] = None
    if expiry_str:
        try:
            expiry_dt = datetime.fromisoformat(str(expiry_str))
        except Exception:
            expiry_dt = None
    if expiry_dt and expiry_dt.tzinfo is None:
        expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
    if expiry_str is None and position.dte is not None:
        try:
            expiry_dt = datetime.now(timezone.utc) + timedelta(days=float(position.dte))
            expiry_str = expiry_dt.date().isoformat()
        except Exception:
            expiry_dt = None
    return expiry_str, expiry_dt


def _option_mid(row: Dict[str, Any]) -> Optional[float]:
    if not row:
        return None
    for key in ("mid", "mark"):
        if row.get(key) is not None:
            try:
                return float(row[key])
            except Exception:
                continue
    bid = row.get("bid")
    ask = row.get("ask")
    try:
        bid_f = float(bid) if bid is not None else None
        ask_f = float(ask) if ask is not None else None
    except Exception:
        return None
    if bid_f is not None and ask_f is not None and ask_f > 0:
        return (bid_f + ask_f) / 2.0
    return None


def _quote_price(q: Dict[str, Any]) -> Optional[float]:
    if not q:
        return None
    for key in ("mark", "mid", "last"):
        if q.get(key) is not None:
            try:
                return float(q[key])
            except Exception:
                continue
    bid = q.get("bid")
    ask = q.get("ask")
    try:
        if bid is not None and ask is not None:
            return (float(bid) + float(ask)) / 2.0
    except Exception:
        return None
    return None


def _nearest_quote(options: List[Dict[str, Any]], strike: float) -> Optional[Dict[str, Any]]:
    best = None
    best_diff = float("inf")
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


def _current_mid_for_position(position: PaperPosition, provider: Any, expiry: Optional[str]) -> Tuple[float, List[Dict[str, Any]]]:
    trade_symbol = position.trade_symbol or position.symbol
    legs = position.legs or []
    chain: Dict[str, Any] = {}
    if provider is not None and hasattr(provider, "get_option_chain"):
        try:
            chain = provider.get_option_chain(trade_symbol, expiry=expiry) or {}
        except Exception as exc:
            log.warning("[position_monitor] get_option_chain failed for %s: %s", trade_symbol, exc)
    if not chain:
        try:
            chain = get_chain(trade_symbol, expiry=expiry) or {}
        except Exception as exc:
            log.warning("[position_monitor] get_chain failed for %s: %s", trade_symbol, exc)
            return float(position.entry_mid), []

    leg_quotes: List[Dict[str, Any]] = []
    for leg in legs:
        leg_type = (leg.type or "").lower()
        options: List[Dict[str, Any]] = []
        if leg_type == "call":
            options = chain.get("calls") or chain.get("call") or []
        elif leg_type == "put":
            options = chain.get("puts") or chain.get("put") or []
        try:
            strike = float(leg.strike)
        except Exception:
            strike = None
        quote_row = _nearest_quote(options, strike) if strike is not None else None
        mid = _option_mid(quote_row or {})
        leg_quotes.append(
            {
                "side": leg.side,
                "type": leg.type,
                "quantity": leg.quantity,
                "mid": mid,
                "strike": leg.strike,
            }
        )

    net_mid = _net_mid(leg_quotes)
    if net_mid is None:
        try:
            net_mid = float(position.entry_mid)
        except Exception:
            net_mid = 0.0
    return float(net_mid), leg_quotes


def _defined_risk_bounds(
    position: PaperPosition,
    entry_mid: float,
    contract_multiplier: float,
) -> Tuple[Optional[float], Optional[float]]:
    width = position.spread_width
    if width in ("", None) and len(position.legs or []) >= 2:
        short_strike = None
        long_strike = None
        for leg in position.legs:
            if leg.side == "short" and short_strike is None:
                short_strike = leg.strike
            elif leg.side == "long" and long_strike is None:
                long_strike = leg.strike
        try:
            if short_strike is not None and long_strike is not None:
                width = abs(float(short_strike) - float(long_strike))
        except Exception:
            width = None
    if width in ("", None):
        return None, None
    try:
        entry_total = float(entry_mid) * float(contract_multiplier)
        width_total = float(width) * float(contract_multiplier)
    except Exception:
        return None, None
    max_profit_per_contract = entry_total
    max_loss_per_contract = max(width_total - entry_total, 0.0)
    return max_profit_per_contract, max_loss_per_contract


def compute_position_metrics(
    position: PaperPosition,
    now: Optional[datetime] = None,
    provider: Any = None,
    vol_snapshot: Optional[Dict[str, float]] = None,
    exit_rules: Optional[ExitRulesConfig] = None,
) -> PositionMetrics:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    provider = provider or get_provider()
    exit_rules = exit_rules or load_exit_rules(position.strategy_id or "")
    contract_multiplier = float(getattr(position, "contract_multiplier", 100.0) or 100.0)

    expiry_str, expiry_dt = _parse_expiry(position)
    if expiry_str is None and position.dte is not None:
        expiry_str = _nearest_expiry(int(position.dte))
    dte_val = None
    if expiry_dt is not None:
        expiry_dt = expiry_dt if expiry_dt.tzinfo is not None else expiry_dt.replace(tzinfo=timezone.utc)
        now_dt = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        dte_val = (expiry_dt - now_dt).total_seconds() / 86400.0
    elif position.dte is not None:
        try:
            dte_val = float(position.dte)
        except Exception:
            dte_val = None

    underlying_price: Optional[float] = None
    if provider is not None and hasattr(provider, "get_quote"):
        try:
            q = provider.get_quote(position.symbol or position.trade_symbol)
            underlying_price = _quote_price(q or {})
        except Exception:
            underlying_price = None
    if underlying_price is None:
        try:
            underlying_price = float(last_price(position.symbol or position.trade_symbol))
        except Exception:
            underlying_price = None
    if underlying_price is None:
        try:
            underlying_price = float(position.underlying_price_hint) if position.underlying_price_hint is not None else 0.0
        except Exception:
            underlying_price = 0.0

    current_mid, leg_quotes = _current_mid_for_position(position, provider, expiry_str)

    try:
        entry_mid = float(position.entry_mid)
    except Exception:
        entry_mid = 0.0

    qty = int(position.qty or 1)

    max_profit_per_contract, max_loss_per_contract = _defined_risk_bounds(
        position,
        entry_mid=entry_mid,
        contract_multiplier=contract_multiplier,
    )

    if (
        max_profit_per_contract is None
        and exit_rules.profit_target_basis == "credit"
        and exit_rules.is_short_premium
    ):
        credit_per_contract = entry_mid * contract_multiplier if entry_mid is not None else None
        if credit_per_contract is not None and credit_per_contract > 0:
            max_profit_per_contract = credit_per_contract

    max_profit_total = position.max_profit_total
    if max_profit_total is None and max_profit_per_contract is not None:
        max_profit_total = max_profit_per_contract * qty
    max_loss_total = position.max_loss_total
    if max_loss_total is None and max_loss_per_contract is not None:
        max_loss_total = max_loss_per_contract * qty

    unrealized_pl_per_contract = (entry_mid - current_mid) * contract_multiplier if entry_mid is not None else None
    unrealized_pl_total = unrealized_pl_per_contract * qty if unrealized_pl_per_contract is not None else None

    pnl_pct_of_max_profit = None
    if unrealized_pl_total is not None and max_profit_total not in (None, 0):
        pnl_pct_of_max_profit = unrealized_pl_total / max_profit_total
    pnl_pct_of_max_loss = None
    if unrealized_pl_total is not None and max_loss_total not in (None, 0):
        pnl_pct_of_max_loss = unrealized_pl_total / max_loss_total

    iv = None
    ivr = None
    if hasattr(provider, "get_ivr"):
        try:
            ivr = provider.get_ivr(position.symbol)
        except Exception:
            ivr = None
    if ivr is None:
        snapshot = vol_snapshot or load_snapshot()
        ivr = snapshot.get((position.symbol or "").upper()) if snapshot else None
    ivr = _to_percent(ivr)

    return PositionMetrics(
        position_id=str(position.id),
        symbol=position.symbol,
        trade_symbol=position.trade_symbol or position.symbol,
        strategy_id=position.strategy_id or position.strategy or "",
        universe_id=position.universe_id,
        underlying_price=float(underlying_price),
        entry_mid=float(entry_mid),
        current_mid=float(current_mid),
        unrealized_pl_per_contract=unrealized_pl_per_contract,
        unrealized_pl_total=unrealized_pl_total,
        max_profit_per_contract=max_profit_per_contract,
        max_profit_total=max_profit_total,
        max_loss_per_contract=max_loss_per_contract,
        max_loss_total=max_loss_total,
        pnl_pct_of_max_profit=pnl_pct_of_max_profit,
        pnl_pct_of_max_loss=pnl_pct_of_max_loss,
        expiry=expiry_dt,
        dte=dte_val,
        as_of=now,
        iv=iv,
        ivr=ivr,
        is_short_premium=exit_rules.is_short_premium,
        strategy_family=exit_rules.strategy_family,
    )


def evaluate_exit_rules(metrics: PositionMetrics, rules: ExitRulesConfig) -> ExitDecision:
    triggered_rules: List[str] = []
    action = "hold"
    reason = "HOLD"

    if rules.profit_target_basis == "credit":
        if metrics.max_profit_total not in (None, 0) and metrics.unrealized_pl_total is not None:
            profit_pct = metrics.unrealized_pl_total / metrics.max_profit_total
            if profit_pct >= rules.profit_target_pct:
                action = "exit"
                reason = "TARGET_PROFIT_HIT"
                triggered_rules.append(
                    f"Profit target {rules.profit_target_pct:.0%} of credit reached ({profit_pct:.1%})"
                )
    elif rules.profit_target_basis == "max_profit":
        if metrics.pnl_pct_of_max_profit is not None:
            if metrics.pnl_pct_of_max_profit >= rules.profit_target_pct:
                action = "exit"
                reason = "TARGET_PROFIT_HIT"
                triggered_rules.append(
                    f"Profit target {rules.profit_target_pct:.0%} of max profit reached ({metrics.pnl_pct_of_max_profit:.1%})"
                )

    if metrics.dte is not None and metrics.dte <= rules.dte_exit:
        if action != "exit":
            action = "exit"
            reason = "DTE_BELOW_THRESHOLD"
        triggered_rules.append(f"DTE {metrics.dte:.1f} <= {rules.dte_exit} days – mechanical DTE exit")

    if rules.is_short_premium and rules.ivr_soft_exit_below is not None and metrics.ivr is not None:
        if metrics.ivr < rules.ivr_soft_exit_below:
            if action != "exit":
                reason = "IVR_BELOW_SOFT_EXIT"
            triggered_rules.append(
                f"IVR {metrics.ivr:.1f} < {rules.ivr_soft_exit_below:.1f} – short premium soft-exit environment"
            )

    return ExitDecision(
        position_id=metrics.position_id,
        action=action,
        reason=reason,
        triggered_rules=triggered_rules,
        notes=None,
    )


def check_exit_signals(position: PaperPosition, rules: ExitRulesConfig, now: Optional[datetime] = None) -> List[str]:
    """
    Convenience wrapper that returns human-readable exit signals for a position.
    """
    metrics = compute_position_metrics(position, exit_rules=rules, now=now)
    decision = evaluate_exit_rules(metrics, rules)

    reasons: List[str] = []
    if decision.action == "exit" and decision.reason != "HOLD":
        reasons.append(decision.reason)
    reasons.extend(decision.triggered_rules)
    return reasons
