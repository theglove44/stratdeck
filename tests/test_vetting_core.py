import pytest

from stratdeck.vetting import VetVerdict, VettingInputs, vet_from_inputs


def _base_inputs(**overrides):
    data = dict(
        symbol="SPX",
        strategy_id="short_put_spread_index_45d",
        strategy_type="short_put_spread",
        direction="bullish",
        dte=45,
        spread_width=5.0,
        short_delta=0.30,
        ivr=0.45,
        pop=0.62,
        credit_per_width=0.30,
        dte_target=45,
        dte_min=40,
        dte_max=50,
        expected_spread_width=5.0,
        target_short_delta=0.30,
        short_delta_min=0.25,
        short_delta_max=0.35,
        ivr_floor=0.25,
        pop_floor=0.55,
        credit_per_width_floor=0.25,
        allowed_trend_regimes=["uptrend", "range"],
        trend_regime="uptrend",
        vol_regime="normal",
    )
    data.update(overrides)
    return VettingInputs(**data)


def test_vetting_accept_strong_candidate():
    inputs = _base_inputs()
    vetting = vet_from_inputs(inputs)

    assert vetting.verdict == VetVerdict.ACCEPT
    assert vetting.score > 70
    assert any("DTE" in r for r in vetting.reasons)
    assert any("IVR" in r or "ivr" in r.lower() for r in vetting.reasons)


def test_vetting_accept_with_missing_regimes():
    inputs = _base_inputs(trend_regime=None, vol_regime=None, allowed_vol_regimes=["normal", "high"])

    vetting = vet_from_inputs(inputs)

    assert vetting.verdict == VetVerdict.ACCEPT
    assert any("regime" in r for r in vetting.reasons)
    assert "ACCEPT" in vetting.rationale


def test_vetting_reject_on_rule_violation():
    inputs = _base_inputs(ivr=0.10)
    vetting = vet_from_inputs(inputs)

    assert vetting.verdict == VetVerdict.REJECT
    assert any("IVR" in r for r in vetting.reasons)


def test_vetting_borderline_case():
    inputs = _base_inputs(credit_per_width=0.251, pop=0.61)
    vetting = vet_from_inputs(inputs)

    assert vetting.verdict == VetVerdict.BORDERLINE
    assert any("borderline" in r.lower() for r in vetting.reasons)
    assert any("credit/width" in r for r in vetting.reasons)
    assert vetting.score < vet_from_inputs(_base_inputs()).score
