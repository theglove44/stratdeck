from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional

from pydantic import BaseModel

from stratdeck.strategies import (
    DTERule,
    ExpiryRules,
    RiskLimits,
    StrategyFilters,
    StrategyTemplate,
    StrategyConfig,
    WidthRuleType,
    load_strategy_config,
)
from stratdeck.tools.filters import FilterDecision


def _to_date(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


class StrategyRuleSnapshot(BaseModel):
    """
    Snapshot of the human-rule thresholds for a specific strategy template.
    """

    strategy_key: str
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


def _expected_width_from_rule(rule: Any) -> Optional[float]:
    if rule is None:
        return None
    if rule.type == WidthRuleType.FIXED:
        if rule.default is not None:
            return float(rule.default)
        if rule.allowed:
            return float(rule.allowed[0])
    if rule.type == WidthRuleType.INDEX_ALLOWED:
        if rule.default is not None:
            return float(rule.default)
        if rule.allowed:
            return float(min(rule.allowed))
    if rule.type == WidthRuleType.BY_PRICE_BRACKET and rule.brackets:
        sorted_brackets = sorted(
            rule.brackets,
            key=lambda b: float("inf") if b.max_price is None else b.max_price,
        )
        return float(sorted_brackets[0].width)
    return None


def snapshot_for_strategy(strategy_key: str, cfg: Optional[StrategyConfig] = None) -> StrategyRuleSnapshot:
    """
    Build a StrategyRuleSnapshot for the given strategy identifier using the
    existing strategies.yaml configuration.
    """

    cfg = cfg or load_strategy_config()
    match: Optional[StrategyTemplate] = None
    for s in cfg.strategies:
        if str(s.name).lower() == str(strategy_key).lower():
            match = s
            break

    if match is None:
        raise KeyError(f"Strategy {strategy_key!r} not found in strategies config")

    dte_rule = getattr(match, "dte", None)
    delta_rule = getattr(match, "delta", None)
    short_leg = getattr(delta_rule, "short_leg", None)
    filters = getattr(match, "filters", None)

    return StrategyRuleSnapshot(
        strategy_key=match.name,
        dte_target=getattr(dte_rule, "target", None),
        dte_min=getattr(dte_rule, "min", None),
        dte_max=getattr(dte_rule, "max", None),
        expected_spread_width=_expected_width_from_rule(getattr(match, "width_rule", None)),
        target_short_delta=getattr(short_leg, "target", None),
        short_delta_min=getattr(short_leg, "min", None),
        short_delta_max=getattr(short_leg, "max", None),
        ivr_floor=getattr(filters, "min_ivr", None),
        pop_floor=getattr(filters, "min_pop", None),
        credit_per_width_floor=getattr(filters, "min_credit_per_width", None),
        allowed_trend_regimes=getattr(match, "allowed_trend_regimes", None),
        allowed_vol_regimes=getattr(match, "allowed_vol_regimes", None),
    )


class HumanRulesFilter:
    """
    Enforces the documented human rules for entry selection.
    Returns FilterDecision with human-readable rejection reasons.
    """

    def __init__(self, strategy: StrategyTemplate):
        self.strategy = strategy

    @staticmethod
    def _normalize_trend(trend: Optional[str]) -> Optional[str]:
        trend = (trend or "").lower() or None
        if trend is None:
            return None
        return {
            "choppy_trend": "chop",
            "range": "sideways",
        }.get(trend, trend)

    @staticmethod
    def _normalize_vol(vol: Optional[str]) -> Optional[str]:
        vol = (vol or "").lower() or None
        if vol is None:
            return None
        return {
            "compression": "normal",
        }.get(vol, vol)

    def _check_dte(self, dte_rule: Optional[DTERule], dte: Optional[int], reasons: List[str], applied: Dict[str, float]) -> None:
        if dte_rule is None or dte is None:
            return
        if dte_rule.min is not None:
            applied["dte_min"] = float(dte_rule.min)
            if dte < dte_rule.min:
                reasons.append(f"DTE {dte} outside allowed range [{dte_rule.min}, {dte_rule.max}]")
                return
        if dte_rule.max is not None:
            applied["dte_max"] = float(dte_rule.max)
            if dte > dte_rule.max:
                reasons.append(f"DTE {dte} outside allowed range [{dte_rule.min}, {dte_rule.max}]")

    def _check_expiry_rules(
        self,
        expiry_rules: Optional[ExpiryRules],
        expiry_is_monthly: Optional[bool],
        expiry_date: Optional[datetime],
        earnings_date: Optional[datetime],
        reasons: List[str],
    ) -> None:
        if expiry_rules is None:
            return
        if expiry_rules.monthlies_only and expiry_is_monthly is False:
            reasons.append("Weekly expiry not allowed (monthlies_only = true)")
        if expiry_rules.monthlies_only and expiry_is_monthly is None and expiry_date is not None:
            # Try to infer from calendar: treat 3rd Friday as monthly.
            weekday = expiry_date.weekday()
            if weekday == 4 and 15 <= expiry_date.day <= 21:
                inferred_monthly = True
            else:
                inferred_monthly = False
            if not inferred_monthly:
                reasons.append("Weekly expiry not allowed (monthlies_only = true)")

        if (
            expiry_rules.earnings_buffer_days is not None
            and earnings_date is not None
            and expiry_date is not None
        ):
            days_before_expiry = (expiry_date.date() - earnings_date.date()).days
            if 0 <= days_before_expiry < expiry_rules.earnings_buffer_days:
                reasons.append(
                    f"Earnings within {days_before_expiry} days of expiry (< {expiry_rules.earnings_buffer_days} buffer)"
                )

    def _check_filters(
        self,
        filters: Optional[StrategyFilters],
        pop: Optional[float],
        ivr: Optional[float],
        credit_per_width: Optional[float],
        reasons: List[str],
        applied: Dict[str, float],
    ) -> None:
        if filters is None:
            return
        if filters.min_pop is not None:
            applied["min_pop"] = float(filters.min_pop)
            if pop is None or pop < filters.min_pop:
                reasons.append(
                    f"POP {float(pop) if pop is not None else 'NA'} < minimum {filters.min_pop}"
                )
                return
        if filters.min_credit_per_width is not None:
            applied["min_credit_per_width"] = float(filters.min_credit_per_width)
            if credit_per_width is None or credit_per_width < filters.min_credit_per_width:
                reasons.append(
                    f"Credit/width {float(credit_per_width) if credit_per_width is not None else 'NA'} < minimum {filters.min_credit_per_width}"
                )
                return
        if filters.min_ivr is not None:
            applied["min_ivr"] = float(filters.min_ivr)
            if ivr is None or ivr < filters.min_ivr:
                reasons.append(
                    f"IV Rank {float(ivr) if ivr is not None else 'NA'} < minimum {filters.min_ivr}"
                )
        if filters.max_ivr is not None and ivr is not None:
            applied["max_ivr"] = float(filters.max_ivr)
            if ivr > filters.max_ivr:
                reasons.append(
                    f"IV Rank {float(ivr)} > maximum {filters.max_ivr}"
                )

    def _check_deltas(
        self,
        short_put_delta: Optional[float],
        short_call_delta: Optional[float],
        reasons: List[str],
    ) -> None:
        delta_rule = getattr(self.strategy, "delta", None)
        band = getattr(delta_rule, "short_leg", None)
        if band is None:
            return
        min_d = band.min
        max_d = band.max
        target = band.target
        if short_put_delta is not None:
            if (min_d is not None and short_put_delta < min_d) or (max_d is not None and short_put_delta > max_d):
                reasons.append(
                    f"Short leg delta {short_put_delta:.2f} outside [{min_d}, {max_d}]"
                )
            elif target is not None and abs(short_put_delta - target) > 0.10:
                # informational only; do not reject
                pass
        if self.strategy.option_type == "both" and short_call_delta is not None:
            if (min_d is not None and short_call_delta < min_d) or (max_d is not None and short_call_delta > max_d):
                reasons.append(
                    f"Short call delta {short_call_delta:.2f} outside [{min_d}, {max_d}]"
                )

    def _check_width(self, width: Optional[float], reasons: List[str]) -> None:
        width_rule = getattr(self.strategy, "width_rule", None)
        if width_rule is None or width is None:
            return
        target_width = None
        if width_rule.default is not None:
            target_width = float(width_rule.default)
        elif width_rule.allowed:
            target_width = float(width_rule.allowed[0])
        if target_width is None:
            return
        if width_rule.type.value == "fixed":
            if width - target_width > 1e-6:
                reasons.append(f"Width {width} exceeds allowed {target_width}")
            return
        if width > target_width:
            reasons.append(f"Width {width} exceeds allowed {target_width}")

    def _check_risk_limits(
        self,
        risk_limits: Optional[RiskLimits],
        buying_power: Optional[float],
        position_delta: Optional[float],
        existing_positions: Optional[int],
        reasons: List[str],
    ) -> None:
        if risk_limits is None:
            return
        if risk_limits.max_buying_power is not None and buying_power is not None:
            if buying_power > risk_limits.max_buying_power:
                reasons.append(
                    f"Buying power {buying_power} > max_buying_power {risk_limits.max_buying_power}"
                )
        if (
            risk_limits.max_positions_per_symbol is not None
            and existing_positions is not None
            and existing_positions >= risk_limits.max_positions_per_symbol
        ):
            reasons.append(
                f"Open positions {existing_positions} >= max_positions_per_symbol {risk_limits.max_positions_per_symbol}"
            )
        if risk_limits.max_position_delta is not None and position_delta is not None:
            if abs(position_delta) > risk_limits.max_position_delta:
                reasons.append(
                    f"Net position delta {position_delta:.2f} exceeds max_position_delta {risk_limits.max_position_delta}"
                )

    def _check_regimes(
        self,
        trend_regime: Optional[str],
        vol_regime: Optional[str],
        reasons: List[str],
    ) -> None:
        trend_regime = self._normalize_trend(trend_regime)
        vol_regime = self._normalize_vol(vol_regime)

        allowed_trend = self.strategy.allowed_trend_regimes or None
        allowed_vol = self.strategy.allowed_vol_regimes or None
        blocked_trend = self.strategy.blocked_trend_regimes or None
        blocked_vol = self.strategy.blocked_vol_regimes or None

        if allowed_trend is not None:
            if trend_regime is None:
                reasons.append("trend_regime is missing but allowed_trend_regimes is configured")
            elif trend_regime not in allowed_trend:
                reasons.append(
                    f"trend_regime {trend_regime!r} not in allowed_trend_regimes {allowed_trend!r}"
                )
        if allowed_vol is not None:
            if vol_regime is None:
                reasons.append("vol_regime is missing but allowed_vol_regimes is configured")
            elif vol_regime not in allowed_vol:
                reasons.append(
                    f"vol_regime {vol_regime!r} not in allowed_vol_regimes {allowed_vol!r}"
                )
        if blocked_trend is not None and trend_regime is not None:
            if trend_regime in blocked_trend:
                reasons.append(
                    f"trend_regime {trend_regime!r} is in blocked_trend_regimes {blocked_trend!r}"
                )
        if blocked_vol is not None and vol_regime is not None:
            if vol_regime in blocked_vol:
                reasons.append(
                    f"vol_regime {vol_regime!r} is in blocked_vol_regimes {blocked_vol!r}"
                )

    def evaluate(self, candidate: Mapping[str, Any]) -> FilterDecision:
        applied: Dict[str, float] = {}
        reasons: List[str] = []

        dte = candidate.get("dte_target") or candidate.get("dte")
        expiry_date = _to_date(candidate.get("expiry") or candidate.get("expiry_date"))
        expiry_is_monthly = candidate.get("expiry_is_monthly")
        earnings_date = _to_date(candidate.get("earnings_date"))
        pop = candidate.get("pop")
        ivr = candidate.get("ivr")
        credit_per_width = candidate.get("credit_per_width")
        spread_width = candidate.get("spread_width") or candidate.get("width")
        short_put_delta = candidate.get("short_put_delta") or candidate.get("short_delta")
        short_call_delta = candidate.get("short_call_delta")
        buying_power = candidate.get("buying_power")
        existing_positions = candidate.get("existing_positions_for_symbol")
        position_delta = candidate.get("position_delta")
        trend_regime = candidate.get("trend_regime")
        vol_regime = candidate.get("vol_regime")

        self._check_dte(getattr(self.strategy, "dte", None), dte, reasons, applied)
        self._check_expiry_rules(
            getattr(self.strategy, "expiry_rules", None),
            expiry_is_monthly,
            expiry_date,
            earnings_date,
            reasons,
        )
        self._check_deltas(short_put_delta, short_call_delta, reasons)
        self._check_width(spread_width, reasons)
        self._check_filters(
            getattr(self.strategy, "filters", None),
            pop,
            ivr,
            credit_per_width,
            reasons,
            applied,
        )
        self._check_regimes(trend_regime, vol_regime, reasons)
        self._check_risk_limits(
            getattr(self.strategy, "risk_limits", None),
            buying_power,
            position_delta,
            existing_positions,
            reasons,
        )

        return FilterDecision(
            passed=len(reasons) == 0,
            applied=applied,
            reasons=reasons,
        )
