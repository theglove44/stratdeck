from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from ..strategies import DTERule, StrategyFilters


@dataclass
class FilterDecision:
    passed: bool
    applied: Dict[str, float]
    reasons: List[str]


def _missing(filter_name: str, metric_label: str) -> str:
    return f"{filter_name} check failed: {metric_label} is missing"


def evaluate_candidate_filters(
    candidate: Mapping[str, Any],
    filters: Optional[StrategyFilters],
    dte_rule: Optional[DTERule] = None,
) -> FilterDecision:
    """
    Evaluate a candidate idea against strategy-level filters and an optional DTE rule.
    """
    if filters is None:
        return FilterDecision(passed=True, applied={}, reasons=[])

    pop = candidate.get("pop")
    ivr = candidate.get("ivr")
    credit_per_width = candidate.get("credit_per_width")
    dte = candidate.get("dte_target")

    applied: Dict[str, float] = {}
    reasons: List[str] = []

    # POP ------------------------------------------------------------------
    if filters.min_pop is not None:
        applied["min_pop"] = float(filters.min_pop)
        if pop is None:
            reasons.append(_missing("min_pop", "pop"))
        elif pop < filters.min_pop:
            reasons.append(f"min_pop {float(pop):.2f} < {float(filters.min_pop):.2f}")

    if filters.max_pop is not None:
        applied["max_pop"] = float(filters.max_pop)
        if pop is None:
            reasons.append(_missing("max_pop", "pop"))
        elif pop > filters.max_pop:
            reasons.append(f"max_pop {float(pop):.2f} > {float(filters.max_pop):.2f}")

    # IVR ------------------------------------------------------------------
    if filters.min_ivr is not None:
        applied["min_ivr"] = float(filters.min_ivr)
        if ivr is None:
            reasons.append(_missing("min_ivr", "ivr"))
        elif ivr < filters.min_ivr:
            reasons.append(f"min_ivr {float(ivr):.2f} < {float(filters.min_ivr):.2f}")

    if filters.max_ivr is not None:
        applied["max_ivr"] = float(filters.max_ivr)
        if ivr is None:
            reasons.append(_missing("max_ivr", "ivr"))
        elif ivr > filters.max_ivr:
            reasons.append(f"max_ivr {float(ivr):.2f} > {float(filters.max_ivr):.2f}")

    # Credit / width -------------------------------------------------------
    if filters.min_credit_per_width is not None:
        applied["min_credit_per_width"] = float(filters.min_credit_per_width)
        if credit_per_width is None:
            reasons.append(_missing("min_credit_per_width", "credit_per_width"))
        elif credit_per_width < filters.min_credit_per_width:
            reasons.append(
                "min_credit_per_width "
                f"{float(credit_per_width):.3f} < {float(filters.min_credit_per_width):.3f}"
            )

    # DTE band -------------------------------------------------------------
    if dte_rule is not None and dte is not None:
        if dte_rule.min is not None:
            applied["dte_min"] = float(dte_rule.min)
            if dte < dte_rule.min:
                reasons.append(f"dte {dte} < dte_min {dte_rule.min}")
        if dte_rule.max is not None:
            applied["dte_max"] = float(dte_rule.max)
            if dte > dte_rule.max:
                reasons.append(f"dte {dte} > dte_max {dte_rule.max}")

    passed = len(reasons) == 0
    return FilterDecision(passed=passed, applied=applied, reasons=reasons)
