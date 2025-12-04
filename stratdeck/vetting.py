from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel

from stratdeck.agents.trade_planner import TradeIdea, TradeLeg
from stratdeck.filters.human_rules import StrategyRuleSnapshot


class VetVerdict(str, Enum):
    ACCEPT = "ACCEPT"
    BORDERLINE = "BORDERLINE"
    REJECT = "REJECT"


class IdeaVetting(BaseModel):
    score: float
    verdict: VetVerdict
    rationale: str
    reasons: List[str]


class VettingInputs(BaseModel):
    # From TradeIdea
    symbol: Optional[str] = None
    strategy_id: Optional[str] = None
    strategy_type: Optional[str] = None
    direction: Optional[str] = None

    dte: Optional[int] = None
    spread_width: Optional[float] = None
    short_delta: Optional[float] = None
    ivr: Optional[float] = None
    pop: Optional[float] = None
    credit_per_width: Optional[float] = None
    trend_regime: Optional[str] = None
    vol_regime: Optional[str] = None

    # From StrategyRuleSnapshot
    dte_target: Optional[int] = None
    dte_min: Optional[int] = None
    dte_max: Optional[int] = None
    expected_spread_width: Optional[float] = None
    target_short_delta: Optional[float] = None
    short_delta_min: Optional[float] = None
    short_delta_max: Optional[float] = None
    ivr_floor: Optional[float] = None
    pop_floor: Optional[float] = None
    credit_per_width_floor: Optional[float] = None
    allowed_trend_regimes: Optional[List[str]] = None
    allowed_vol_regimes: Optional[List[str]] = None


def _get_value(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj.get(name)
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _extract_short_delta(idea: Any) -> Optional[float]:
    direct = _get_value(idea, "short_put_delta", "short_delta")
    if direct is not None:
        return direct

    legs = _get_value(idea, "short_legs") or []
    for leg in legs:
        delta = getattr(leg, "delta", None) if not isinstance(leg, dict) else leg.get("delta")
        if delta is not None:
            return delta
    return None


def build_vetting_inputs(idea: Any, rules: StrategyRuleSnapshot) -> VettingInputs:
    symbol = _get_value(idea, "symbol", "trade_symbol", "data_symbol")
    strategy_id = _get_value(idea, "strategy_id", "strategy")
    strategy_type = _get_value(idea, "strategy")
    direction = _get_value(idea, "direction")

    dte = _get_value(idea, "dte", "dte_target")
    spread_width = _get_value(idea, "spread_width", "width")
    short_delta = _extract_short_delta(idea)
    ivr = _get_value(idea, "ivr")
    pop = _get_value(idea, "pop")
    credit_per_width = _get_value(idea, "credit_per_width")
    trend_regime = _get_value(idea, "trend_regime")
    vol_regime = _get_value(idea, "vol_regime")

    return VettingInputs(
        symbol=symbol,
        strategy_id=strategy_id,
        strategy_type=strategy_type,
        direction=direction,
        dte=dte,
        spread_width=spread_width,
        short_delta=short_delta,
        ivr=ivr,
        pop=pop,
        credit_per_width=credit_per_width,
        trend_regime=trend_regime,
        vol_regime=vol_regime,
        dte_target=rules.dte_target,
        dte_min=rules.dte_min,
        dte_max=rules.dte_max,
        expected_spread_width=rules.expected_spread_width,
        target_short_delta=rules.target_short_delta,
        short_delta_min=rules.short_delta_min,
        short_delta_max=rules.short_delta_max,
        ivr_floor=rules.ivr_floor,
        pop_floor=rules.pop_floor,
        credit_per_width_floor=rules.credit_per_width_floor,
        allowed_trend_regimes=rules.allowed_trend_regimes,
        allowed_vol_regimes=rules.allowed_vol_regimes,
    )


def _score_above_floor(value: Optional[float], floor: Optional[float], max_points: float) -> float:
    if value is None or floor is None:
        return 0.0
    margin = value - floor
    if margin <= 0:
        return -5.0
    if margin < 0.02:
        return max_points * 0.4
    if margin < 0.05:
        return max_points * 0.7
    return max_points


def _score_band(value: Optional[float], target: Optional[float], min_v: Optional[float], max_v: Optional[float], max_points: float) -> float:
    if value is None:
        return 0.0
    if min_v is not None and value < min_v:
        return -5.0
    if max_v is not None and value > max_v:
        return -5.0
    if target is None:
        return max_points * 0.6
    diff = abs(value - target)
    if diff < 0.02:
        return max_points
    if diff < 0.05:
        return max_points * 0.7
    return max_points * 0.4


def _score_window(value: Optional[int], target: Optional[int], min_v: Optional[int], max_v: Optional[int], max_points: float) -> float:
    if value is None:
        return 0.0
    if min_v is not None and value < min_v:
        return -5.0
    if max_v is not None and value > max_v:
        return -5.0
    if target is None:
        return max_points * 0.6
    diff = abs(value - target)
    if diff <= 1:
        return max_points
    if diff <= 3:
        return max_points * 0.7
    return max_points * 0.4


def vet_from_inputs(inputs: VettingInputs) -> IdeaVetting:
    violations: List[str] = []
    borderline_flags: List[str] = []
    regime_flags: List[str] = []
    notes: List[str] = []
    borderline_for_score = False

    def _val_str(val: Any, default: str = "NA") -> str:
        if val is None:
            return default
        try:
            if isinstance(val, float):
                return f"{val:.2f}"
            return str(val)
        except Exception:
            return default

    # DTE checks
    if inputs.dte is not None and (inputs.dte_min is not None or inputs.dte_max is not None):
        window = [inputs.dte_min, inputs.dte_max]
        if inputs.dte_min is not None and inputs.dte < inputs.dte_min:
            violations.append(f"DTE {inputs.dte} below window [{inputs.dte_min}, {inputs.dte_max}]")
        elif inputs.dte_max is not None and inputs.dte > inputs.dte_max:
            violations.append(f"DTE {inputs.dte} above window [{inputs.dte_min}, {inputs.dte_max}]")
        else:
            notes.append(f"DTE {inputs.dte} within [{inputs.dte_min}, {inputs.dte_max}]")
            if inputs.dte_min is not None and abs(inputs.dte - inputs.dte_min) <= 1:
                borderline_for_score = True
                borderline_flags.append(
                    f"DTE {inputs.dte} near min {inputs.dte_min} – borderline"
                )
            if inputs.dte_max is not None and abs(inputs.dte - inputs.dte_max) <= 1:
                borderline_for_score = True
                borderline_flags.append(
                    f"DTE {inputs.dte} near max {inputs.dte_max} – borderline"
                )
    elif inputs.dte is not None:
        notes.append(f"DTE {inputs.dte}")
    else:
        notes.append("DTE missing")

    # Width check
    if inputs.expected_spread_width is not None and inputs.spread_width is not None:
        if inputs.spread_width - inputs.expected_spread_width > 1e-6:
            violations.append(
                f"Width {inputs.spread_width} exceeds allowed {inputs.expected_spread_width}"
            )
        else:
            notes.append(
                f"Width {inputs.spread_width} matches expected {inputs.expected_spread_width}"
            )
    elif inputs.spread_width is not None:
        notes.append(f"Width {inputs.spread_width}")

    # Delta checks
    if inputs.short_delta is not None and (inputs.short_delta_min is not None or inputs.short_delta_max is not None):
        if inputs.short_delta_min is not None and inputs.short_delta < inputs.short_delta_min:
            violations.append(
                f"Short leg delta {inputs.short_delta:.2f} below min {inputs.short_delta_min}"
            )
        elif inputs.short_delta_max is not None and inputs.short_delta > inputs.short_delta_max:
            violations.append(
                f"Short leg delta {inputs.short_delta:.2f} above max {inputs.short_delta_max}"
            )
        else:
            notes.append(
                f"Short delta {inputs.short_delta:.2f} within [{inputs.short_delta_min}, {inputs.short_delta_max}]"
            )
            if inputs.short_delta_min is not None and abs(inputs.short_delta - inputs.short_delta_min) <= 0.02:
                borderline_for_score = True
                borderline_flags.append(
                    f"Short delta {inputs.short_delta:.2f} near min {inputs.short_delta_min:.2f} – borderline"
                )
            if inputs.short_delta_max is not None and abs(inputs.short_delta - inputs.short_delta_max) <= 0.02:
                borderline_for_score = True
                borderline_flags.append(
                    f"Short delta {inputs.short_delta:.2f} near max {inputs.short_delta_max:.2f} – borderline"
                )
    elif inputs.short_delta is not None:
        notes.append(f"Short delta {inputs.short_delta:.2f}")

    # Floors
    if inputs.ivr_floor is not None:
        if inputs.ivr is None or inputs.ivr < inputs.ivr_floor:
            violations.append(
                f"IVR {_val_str(inputs.ivr)} below floor {_val_str(inputs.ivr_floor)}"
            )
        else:
            notes.append(
                f"IVR {_val_str(inputs.ivr)} >= floor {_val_str(inputs.ivr_floor)}"
            )
            if inputs.ivr - inputs.ivr_floor <= 0.02:
                borderline_for_score = True
                borderline_flags.append(
                    f"IVR {_val_str(inputs.ivr)} only slightly above floor {_val_str(inputs.ivr_floor)} – borderline"
                )

    if inputs.pop_floor is not None:
        if inputs.pop is None or inputs.pop < inputs.pop_floor:
            violations.append(
                f"POP {_val_str(inputs.pop)} below floor {_val_str(inputs.pop_floor)}"
            )
        else:
            notes.append(
                f"POP {_val_str(inputs.pop)} >= floor {_val_str(inputs.pop_floor)}"
            )
            if inputs.pop - inputs.pop_floor <= 0.02:
                borderline_for_score = True
                borderline_flags.append(
                    f"POP {_val_str(inputs.pop)} only slightly above floor {_val_str(inputs.pop_floor)} – borderline"
                )

    if inputs.credit_per_width_floor is not None:
        if inputs.credit_per_width is None or inputs.credit_per_width < inputs.credit_per_width_floor:
            violations.append(
                "credit/width "
                f"{_val_str(inputs.credit_per_width)} below floor {_val_str(inputs.credit_per_width_floor)}"
            )
        else:
            notes.append(
                f"credit/width {_val_str(inputs.credit_per_width)} >= floor {_val_str(inputs.credit_per_width_floor)}"
            )
            if inputs.credit_per_width - inputs.credit_per_width_floor <= 0.01:
                borderline_for_score = True
                borderline_flags.append(
                    "credit/width "
                    f"{_val_str(inputs.credit_per_width)} only slightly above floor {_val_str(inputs.credit_per_width_floor)} – borderline"
                )

    # Regimes
    if inputs.allowed_trend_regimes is not None:
        if inputs.trend_regime is None:
            borderline_for_score = True
            regime_flags.append("trend_regime missing while allowlist configured")
        elif inputs.trend_regime not in inputs.allowed_trend_regimes:
            violations.append(
                f"trend_regime {inputs.trend_regime!r} not allowed {inputs.allowed_trend_regimes!r}"
            )
        else:
            notes.append(f"trend_regime {inputs.trend_regime}")

    if inputs.allowed_vol_regimes is not None:
        if inputs.vol_regime is None:
            borderline_for_score = True
            regime_flags.append("vol_regime missing while allowlist configured")
        elif inputs.vol_regime not in inputs.allowed_vol_regimes:
            violations.append(
                f"vol_regime {inputs.vol_regime!r} not allowed {inputs.allowed_vol_regimes!r}"
            )
        else:
            notes.append(f"vol_regime {inputs.vol_regime}")

    score = 50.0
    score -= len(violations) * 15.0
    score += _score_window(inputs.dte, inputs.dte_target, inputs.dte_min, inputs.dte_max, 10.0)
    score += _score_above_floor(inputs.ivr, inputs.ivr_floor, 12.0)
    score += _score_above_floor(inputs.pop, inputs.pop_floor, 12.0)
    score += _score_above_floor(inputs.credit_per_width, inputs.credit_per_width_floor, 10.0)
    score += _score_band(inputs.short_delta, inputs.target_short_delta, inputs.short_delta_min, inputs.short_delta_max, 8.0)

    if borderline_for_score and not violations:
        score -= 5.0

    score = max(0.0, min(100.0, score))

    if violations:
        verdict = VetVerdict.REJECT
    elif borderline_flags:
        verdict = VetVerdict.BORDERLINE
    else:
        verdict = VetVerdict.ACCEPT

    reasons: List[str] = []
    reasons.extend(violations)
    reasons.extend(borderline_flags)
    reasons.extend(regime_flags)
    reasons.extend(notes)

    descriptor = inputs.strategy_id or inputs.strategy_type or "idea"
    name = inputs.symbol or "?"
    rationale_parts = []
    if inputs.dte is not None:
        rationale_parts.append(f"DTE {inputs.dte}")
    if inputs.spread_width is not None:
        rationale_parts.append(f"width {_val_str(inputs.spread_width)}")
    if inputs.short_delta is not None:
        rationale_parts.append(f"short Δ {_val_str(inputs.short_delta)}")
    if inputs.ivr is not None and inputs.ivr_floor is not None:
        rationale_parts.append(f"IVR {_val_str(inputs.ivr)} vs floor {_val_str(inputs.ivr_floor)}")
    if inputs.pop is not None and inputs.pop_floor is not None:
        rationale_parts.append(f"POP {_val_str(inputs.pop)} vs floor {_val_str(inputs.pop_floor)}")
    if inputs.credit_per_width is not None and inputs.credit_per_width_floor is not None:
        rationale_parts.append(
            f"cpw {_val_str(inputs.credit_per_width)} vs floor {_val_str(inputs.credit_per_width_floor)}"
        )

    rationale_body = ", ".join(rationale_parts)
    rationale = f"{name} {descriptor}: {rationale_body} – {verdict.value}."
    flag_descriptions: List[str] = []
    if borderline_flags:
        flag_descriptions.append("borderline metrics: " + "; ".join(borderline_flags))
    if regime_flags:
        flag_descriptions.append("regime notes: " + "; ".join(regime_flags))
    if flag_descriptions:
        rationale = f"{rationale} {' '.join(flag_descriptions)}"

    return IdeaVetting(score=score, verdict=verdict, rationale=rationale, reasons=reasons)


def vet_single_idea(idea: Any, rules: StrategyRuleSnapshot) -> IdeaVetting:
    inputs = build_vetting_inputs(idea, rules)
    return vet_from_inputs(inputs)


def vet_batch(
    ideas: Sequence[Any],
    rules_lookup: Callable[[str], StrategyRuleSnapshot],
) -> List[Tuple[Any, IdeaVetting]]:
    vetted: List[Tuple[Any, IdeaVetting]] = []
    for idea in ideas:
        strategy_key = _get_value(idea, "strategy_id", "strategy")
        if not strategy_key:
            continue
        try:
            rules = rules_lookup(strategy_key)
        except Exception:
            continue
        vetting = vet_single_idea(idea, rules)
        vetted.append((idea, vetting))
    return vetted
