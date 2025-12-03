import pytest

from stratdeck.filters import HumanRulesFilter
from stratdeck.strategies import (
    DTERule,
    DeltaBand,
    DeltaRule,
    ExpiryRules,
    ProductType,
    RiskLimits,
    StrategyFilters,
    StrategyTemplate,
    WidthRule,
    WidthRuleType,
)


def _strategy():
    return StrategyTemplate(
        name="short_put_spread_index_45d",
        applies_to_universes=["index_core"],
        product_type=ProductType.INDEX,
        dte=DTERule(min=40, max=50),
        expiry_rules=ExpiryRules(monthlies_only=True, earnings_buffer_days=21),
        delta=DeltaRule(short_leg=DeltaBand(target=0.30, min=0.25, max=0.35)),
        width_rule=WidthRule(type=WidthRuleType.FIXED, default=5, allowed=[5]),
        filters=StrategyFilters(min_pop=0.60, min_credit_per_width=0.25, min_ivr=0.25),
        risk_limits=RiskLimits(
            max_buying_power=500,
            max_positions_per_symbol=1,
            max_position_delta=2.0,
        ),
        allowed_trend_regimes=["uptrend"],
    )


def test_human_rules_pass_when_all_constraints_met():
    strategy = _strategy()
    filt = HumanRulesFilter(strategy)
    decision = filt.evaluate(
        {
            "dte_target": 45,
            "pop": 0.65,
            "ivr": 0.30,
            "credit_per_width": 0.30,
            "spread_width": 5.0,
            "short_put_delta": 0.30,
            "buying_power": 300.0,
            "position_delta": 1.0,
            "expiry_is_monthly": True,
            "trend_regime": "uptrend",
        }
    )

    assert decision.passed is True
    assert decision.reasons == []
    assert decision.applied.get("min_pop") == pytest.approx(0.60)


def test_human_rules_reject_weekly_and_rich_risk():
    strategy = _strategy()
    filt = HumanRulesFilter(strategy)
    decision = filt.evaluate(
        {
            "dte_target": 38,
            "pop": 0.50,
            "ivr": 0.10,
            "credit_per_width": 0.10,
            "spread_width": 10.0,
            "short_put_delta": 0.10,
            "buying_power": 600.0,
            "position_delta": 3.0,
            "expiry_is_monthly": False,
            "trend_regime": "downtrend",
        }
    )

    assert decision.passed is False
    assert decision.reasons  # should surface at least one violation
    assert any(
        "buying_power" in r.lower() or "dte" in r.lower() or "trend_regime" in r
        for r in decision.reasons
    )
