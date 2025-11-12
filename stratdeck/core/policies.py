# core/policies.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

@dataclass
class PolicyPack:
    # Account level
    account_bp_cap: float = 5000.0  # max buying power used per trade (USD)
    account_bp_available: Optional[float] = None  # optionally pass live/paper BP available

    # Trade level guards
    per_trade_bp_cap: float = 2500.0
    min_credit_per_width: float = 0.20  # e.g., require $0.20 per $1 width
    min_pop: float = 0.55               # POP floor (probability of profit)

    # Structure/width rules
    allowed_widths_index: List[float] = field(default_factory=lambda: [1, 2, 3, 4, 5, 10])
    allowed_widths_equity: List[float] = field(default_factory=lambda: [0.5, 1, 2, 3, 5])

    # Exposure caps
    per_symbol_open_spreads_cap: int = 3

    # Fees
    fee_per_contract_leg: float = 1.25  # rough estimate

    # Free-form extras if needed
    extras: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_config(cfg: Dict[str, Any]) -> "PolicyPack":
        # Merge defaults with overrides from config
        defaults = PolicyPack().__dict__.copy()
        merged = {**defaults, **(cfg or {})}
        # Convert lists if provided as strings in config
        for k in ["allowed_widths_index", "allowed_widths_equity"]:
            v = merged.get(k)
            if isinstance(v, str):
                merged[k] = [float(x.strip()) for x in v.split(",") if x.strip()]
        return PolicyPack(**merged)

@dataclass
class ComplianceViolation:
    code: str
    message: str

@dataclass
class ComplianceResult:
    ok: bool
    violations: List[ComplianceViolation] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return "APPROVED"
        return "VETO: " + "; ".join(f"[{v.code}] {v.message}" for v in self.violations)


def _width_allowed(width: float, is_index: bool, pack: PolicyPack) -> bool:
    allowed = pack.allowed_widths_index if is_index else pack.allowed_widths_equity
    return any(abs(width - w) < 1e-9 for w in allowed)


def check_policies(*,
                   pack: PolicyPack,
                   plan: Any,
                   preview: Any,
                   candidate: Optional[Dict[str, Any]] = None,
                   positions_state: Optional[Dict[str, Any]] = None) -> ComplianceResult:
    """
    plan: OrderPlan-like object with attributes: underlying, is_index, spread_width, credit_per_spread, qty
    preview: preview-like with attributes: max_loss, bp_required, est_fees
    positions_state: { symbol -> open_spreads_count }
    candidate: optional dict containing keys like 'pop' to validate min_pop
    """
    violations: List[ComplianceViolation] = []

    # Width rules
    if not _width_allowed(float(plan.spread_width), bool(plan.is_index), pack):
        violations.append(ComplianceViolation(
            code="WIDTH",
            message=f"Width {plan.spread_width} not in allowed set for {'index' if plan.is_index else 'equity'}"
        ))

    # Credit per width
    if plan.credit_per_spread < pack.min_credit_per_width * plan.spread_width:
        violations.append(ComplianceViolation(
            code="CR",
            message=(f"Credit/width too low: {plan.credit_per_spread:.2f} < "
                     f"{pack.min_credit_per_width:.2f} x {plan.spread_width}")
        ))

    # POP floor
    pop = None
    if candidate:
        pop = candidate.get("pop") or candidate.get("pop_estimate") or candidate.get("pop_pct")
    if pop is not None:
        try:
            pop_val = float(pop)
            if pop_val > 1.0:
                pop_val /= 100.0
        except Exception:
            pop_val = None
        if pop_val is not None and pop_val < pack.min_pop:
            violations.append(ComplianceViolation(
                code="POP",
                message=f"POP {pop_val:.2f} below floor {pack.min_pop:.2f}"
            ))

    # BP constraints
    if preview and getattr(preview, "bp_required", None) is not None:
        bp_req = float(preview.bp_required)
        if bp_req > pack.per_trade_bp_cap:
            violations.append(ComplianceViolation(
                code="BP_TRADE",
                message=f"BP {bp_req:.2f} exceeds per-trade cap {pack.per_trade_bp_cap:.2f}"
            ))
        if pack.account_bp_available is not None and bp_req > float(pack.account_bp_available):
            violations.append(ComplianceViolation(
                code="BP_AVAIL",
                message=f"BP {bp_req:.2f} exceeds available {pack.account_bp_available:.2f}"
            ))
        if bp_req > pack.account_bp_cap:
            violations.append(ComplianceViolation(
                code="BP_ACCT",
                message=f"BP {bp_req:.2f} exceeds account cap {pack.account_bp_cap:.2f}"
            ))

    # Per-symbol exposure cap
    sym = getattr(plan, "underlying", None)
    if positions_state and sym in positions_state:
        existing = int(positions_state.get(sym, 0))
        if existing + int(plan.qty) > pack.per_symbol_open_spreads_cap:
            violations.append(ComplianceViolation(
                code="SYMBOL_CAP",
                message=f"{existing}+{plan.qty} > per-symbol cap {pack.per_symbol_open_spreads_cap}"
            ))

    return ComplianceResult(ok=len(violations) == 0, violations=violations)