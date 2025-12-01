import pytest

from stratdeck.strategies import DTERule, StrategyFilters, StrategyTemplate
from stratdeck.tools.filters import evaluate_candidate_filters


def test_filters_pass_when_all_constraints_satisfied():
    candidate = {
        "pop": 0.60,
        "ivr": 0.35,
        "credit_per_width": 0.40,
        "dte_target": 45,
    }
    filters = StrategyFilters(
        min_pop=0.55,
        max_pop=0.95,
        min_ivr=0.20,
        max_ivr=0.90,
        min_credit_per_width=0.30,
    )
    dte_rule = DTERule(min=30, max=60)

    decision = evaluate_candidate_filters(candidate, filters, dte_rule)

    assert decision.passed is True
    assert decision.reasons == []
    for key in (
        "min_pop",
        "max_pop",
        "min_ivr",
        "max_ivr",
        "min_credit_per_width",
        "dte_min",
        "dte_max",
    ):
        assert key in decision.applied


def test_min_ivr_fails_when_too_low():
    filters = StrategyFilters(min_ivr=0.20)
    candidate = {"pop": 0.6, "ivr": 0.18, "credit_per_width": 0.3}

    decision = evaluate_candidate_filters(candidate, filters)

    assert decision.passed is False
    assert any("min_ivr 0.18" in reason for reason in decision.reasons)


def test_min_pop_failure():
    filters = StrategyFilters(min_pop=0.55)
    candidate = {"pop": 0.52, "ivr": 0.25, "credit_per_width": 0.3}

    decision = evaluate_candidate_filters(candidate, filters)

    assert decision.passed is False
    assert any("min_pop" in reason for reason in decision.reasons)


def test_max_pop_failure():
    filters = StrategyFilters(max_pop=0.70)
    candidate = {"pop": 0.82, "ivr": 0.3, "credit_per_width": 0.25}

    decision = evaluate_candidate_filters(candidate, filters)

    assert decision.passed is False
    assert any("max_pop" in reason for reason in decision.reasons)


def test_min_credit_per_width_failure():
    filters = StrategyFilters(min_credit_per_width=0.20)
    candidate = {"pop": 0.6, "ivr": 0.3, "credit_per_width": 0.18}

    decision = evaluate_candidate_filters(candidate, filters)

    assert decision.passed is False
    assert any("min_credit_per_width" in reason for reason in decision.reasons)


def test_missing_ivr_fails_when_min_ivr_configured():
    filters = StrategyFilters(min_ivr=0.20)
    candidate = {"pop": 0.6, "credit_per_width": 0.25}

    decision = evaluate_candidate_filters(candidate, filters)

    assert decision.passed is False
    assert "min_ivr check failed: ivr is missing" in decision.reasons


def test_dte_rule_failure():
    filters = StrategyFilters()
    dte_rule = DTERule(min=30, max=50)
    candidate = {"pop": 0.6, "ivr": 0.3, "credit_per_width": 0.3, "dte_target": 60}

    decision = evaluate_candidate_filters(candidate, filters, dte_rule)

    assert decision.passed is False
    assert "dte 60 > dte_max 50" in decision.reasons


def test_no_filters_configured_passes():
    decision = evaluate_candidate_filters({}, None)

    assert decision.passed is True
    assert decision.applied == {}
    assert decision.reasons == []


def test_regime_filters_pass_when_in_allowed_lists():
    candidate = {
        "trend_regime": "uptrend",
        "vol_regime": "normal",
        "dte_target": 45,
        "pop": 0.60,
        "ivr": 0.30,
        "credit_per_width": 0.40,
    }

    filters = StrategyFilters(
        min_pop=0.55,
        min_ivr=0.20,
        min_credit_per_width=0.30,
    )

    template = StrategyTemplate(
        name="test_strategy",
        applies_to_universes=["index_core"],
        dte=DTERule(min=30, max=60),
        filters=filters,
        allowed_trend_regimes=["uptrend", "sideways"],
        allowed_vol_regimes=["normal", "high"],
    )

    decision = evaluate_candidate_filters(
        candidate,
        filters=filters,
        dte_rule=template.dte,
        strategy_template=template,
    )

    assert decision.passed is True
    assert decision.reasons == []


def test_regime_filters_fail_on_disallowed_trend():
    candidate = {
        "trend_regime": "downtrend",
        "vol_regime": "normal",
        "pop": 0.60,
        "ivr": 0.30,
        "credit_per_width": 0.40,
        "dte_target": 45,
    }

    filters = StrategyFilters(min_pop=0.55, min_ivr=0.20)

    template = StrategyTemplate(
        name="test_strategy",
        applies_to_universes=["index_core"],
        dte=DTERule(min=30, max=60),
        filters=filters,
        allowed_trend_regimes=["uptrend", "sideways"],
    )

    decision = evaluate_candidate_filters(
        candidate,
        filters=filters,
        dte_rule=template.dte,
        strategy_template=template,
    )

    assert decision.passed is False
    assert any("trend_regime" in reason for reason in decision.reasons)


def test_regime_filters_fail_when_trend_missing_but_required():
    candidate = {
        "vol_regime": "normal",
        "pop": 0.60,
        "ivr": 0.30,
        "credit_per_width": 0.40,
        "dte_target": 45,
    }

    filters = StrategyFilters(min_pop=0.55)

    template = StrategyTemplate(
        name="test_strategy",
        applies_to_universes=["index_core"],
        dte=DTERule(min=30, max=60),
        filters=filters,
        allowed_trend_regimes=["uptrend", "sideways"],
    )

    decision = evaluate_candidate_filters(
        candidate,
        filters=filters,
        dte_rule=template.dte,
        strategy_template=template,
    )

    assert decision.passed is False
    assert any("trend_regime is missing" in reason for reason in decision.reasons)


def test_regime_filters_fail_on_disallowed_vol():
    candidate = {
        "trend_regime": "uptrend",
        "vol_regime": "low",
        "pop": 0.60,
        "ivr": 0.30,
        "credit_per_width": 0.40,
        "dte_target": 45,
    }

    filters = StrategyFilters(min_pop=0.55)

    template = StrategyTemplate(
        name="test_strategy",
        applies_to_universes=["index_core"],
        dte=DTERule(min=30, max=60),
        filters=filters,
        allowed_trend_regimes=["uptrend", "sideways"],
        allowed_vol_regimes=["normal", "high"],
    )

    decision = evaluate_candidate_filters(
        candidate,
        filters=filters,
        dte_rule=template.dte,
        strategy_template=template,
    )

    assert decision.passed is False
    assert any("vol_regime" in reason for reason in decision.reasons)
