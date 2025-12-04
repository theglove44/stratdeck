from stratdeck.agents.trade_planner import TradeIdea, TradeLeg
from stratdeck.filters import snapshot_for_strategy
from stratdeck.vetting import VetVerdict, vet_single_idea


def _sample_trade_idea() -> TradeIdea:
    short_leg = TradeLeg(side="short", type="put", strike=100.0, expiry="2025-01-17", quantity=1, delta=0.30, dte=45)
    long_leg = TradeLeg(side="long", type="put", strike=95.0, expiry="2025-01-17", quantity=1, delta=0.05, dte=45)
    return TradeIdea(
        symbol="SPX",
        data_symbol="SPX",
        trade_symbol="SPX",
        strategy="short_put_spread",
        direction="bullish",
        vol_context="normal",
        rationale="test idea",
        legs=[short_leg, long_leg],
        short_legs=[short_leg],
        long_legs=[long_leg],
        dte=45,
        spread_width=5.0,
        ivr=0.30,
        pop=0.60,
        credit_per_width=0.26,
        short_put_delta=0.30,
        strategy_id="short_put_spread_index_45d",
    )


def test_trade_idea_vetting_with_strategy_snapshot():
    idea = _sample_trade_idea()
    snapshot = snapshot_for_strategy("short_put_spread_index_45d")

    vetting = vet_single_idea(idea, snapshot)

    assert vetting.verdict in {VetVerdict.ACCEPT, VetVerdict.BORDERLINE}
    assert vetting.rationale
    assert vetting.reasons
