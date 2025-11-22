import pytest

from stratdeck.agents.trade_planner import TradePlanner


def _short_and_long(legs):
    short = next(l for l in legs if l.side == "short")
    long = next(l for l in legs if l.side == "long")
    return short, long


def test_spx_strikes_follow_support_levels():
    planner = TradePlanner()
    underlying = 6500.0
    support_levels = [6400.0, 6450.0]
    resistance_levels = [6600.0]

    legs, width = planner._build_legs_from_ta(
        strategy_type="short_put_spread",
        support_levels=support_levels,
        resistance_levels=resistance_levels,
        underlying_hint=underlying,
        dte_target=45,
        width_override=5.0,
    )

    short_leg, long_leg = _short_and_long(legs)
    assert width == pytest.approx(5.0)
    assert short_leg.strike == pytest.approx(6450.0)
    assert long_leg.strike == pytest.approx(6445.0)
    assert 0.9 < short_leg.strike / underlying < 1.0


def test_xsp_strikes_respect_underlying_scale_when_levels_off():
    planner = TradePlanner()
    underlying = 650.0
    # TA levels on SPX scale (10×) should not drive XSP strikes.
    support_levels = [6400.0, 6420.0]
    resistance_levels = [6600.0]

    legs, width = planner._build_legs_from_ta(
        strategy_type="short_put_spread",
        support_levels=support_levels,
        resistance_levels=resistance_levels,
        underlying_hint=underlying,
        dte_target=45,
        width_override=5.0,
    )

    short_leg, long_leg = _short_and_long(legs)
    assert width == pytest.approx(5.0)
    assert short_leg.strike == pytest.approx(underlying - width)
    assert long_leg.strike == pytest.approx(short_leg.strike - width)
    # Ensure we never drift to SPX-scale strikes.
    assert short_leg.strike / underlying < 3.0
