# agents/compliance.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional

from ..core.policies import PolicyPack, check_policies, ComplianceResult
from ..tools.orders import OrderPlan, OrderPreview


@dataclass
class ComplianceAgent:
    pack: PolicyPack
    # positions_state: symbol -> open_spread_count (paper tracking)
    positions_state: Dict[str, int]

    @staticmethod
    def from_config(cfg: Dict[str, Any], positions_state: Optional[Dict[str, int]] = None) -> "ComplianceAgent":
        policy_cfg = cfg.get("policies") if cfg else {}
        pack = PolicyPack.from_config(policy_cfg or {}) if cfg is not None else PolicyPack()
        return ComplianceAgent(pack=pack, positions_state=positions_state or {})

    def approve(self, *, plan: OrderPlan, preview: OrderPreview, candidate: Optional[Dict[str, Any]] = None) -> ComplianceResult:
        return check_policies(
            pack=self.pack,
            plan=plan,
            preview=preview,
            candidate=candidate,
            positions_state=self.positions_state,
        )

    def record_open(self, plan: OrderPlan):
        sym = plan.underlying
        self.positions_state[sym] = int(self.positions_state.get(sym, 0)) + int(plan.qty)
