import datetime
from typing import List

from stratdeck.agents.trade_planner import TradeIdea, TradeLeg
from stratdeck.orchestrator import run_open_cycle
from stratdeck.tools.positions import PaperPosition
from stratdeck.vetting import IdeaVetting, VetVerdict


def _make_idea(symbol: str, strategy_id: str = "short_put_spread_index_45d") -> TradeIdea:
    leg = TradeLeg(side="short", type="put", strike=100.0, expiry="2024-01-19", quantity=1)
    return TradeIdea(
        symbol=symbol,
        data_symbol=symbol,
        trade_symbol=symbol,
        strategy="short_put_spread",
        direction="bullish",
        vol_context="normal",
        rationale="test idea",
        legs=[leg],
        strategy_id=strategy_id,
        universe_id="index_core",
    )


def _dummy_position(idea: TradeIdea, qty: int) -> PaperPosition:
    return PaperPosition(
        symbol=idea.symbol,
        trade_symbol=idea.trade_symbol,
        strategy=idea.strategy,
        strategy_id=idea.strategy_id,
        qty=qty,
        entry_mid=0.5,
        legs=[],
        opened_at=datetime.datetime.now(datetime.timezone.utc),
    )


def test_open_cycle_filters_by_verdict_and_score():
    idea_a = _make_idea("AAA")
    idea_b = _make_idea("BBB")
    idea_c = _make_idea("CCC")
    ideas = [idea_a, idea_b, idea_c]

    def fake_ideas(universe: str, strategy: str) -> List[TradeIdea]:
        return ideas

    def fake_vet(idea, rules):
        if idea is idea_a:
            return IdeaVetting(score=90, verdict=VetVerdict.ACCEPT, rationale="", reasons=[])
        if idea is idea_b:
            return IdeaVetting(score=95, verdict=VetVerdict.BORDERLINE, rationale="", reasons=[])
        return IdeaVetting(score=70, verdict=VetVerdict.ACCEPT, rationale="", reasons=[])

    opened = []

    def fake_open(idea, qty):
        opened.append(idea)
        return _dummy_position(idea, qty)

    result = run_open_cycle(
        universe="U",
        strategy="short_put_spread_index_45d",
        max_trades=5,
        min_score=80,
        idea_generator=fake_ideas,
        vet_one=fake_vet,
        open_from_idea=fake_open,
    )

    assert result.generated_count == 3
    assert result.eligible_count == 1
    assert len(result.opened) == 1
    assert result.opened[0].idea is idea_a
    assert opened == [idea_a]


def test_open_cycle_respects_max_trades_and_sorting():
    idea_low = _make_idea("AAA")
    idea_mid = _make_idea("BBB")
    idea_high = _make_idea("CCC")
    ideas = [idea_low, idea_mid, idea_high]

    scores = {
        "AAA": 70,
        "BBB": 85,
        "CCC": 95,
    }

    def fake_ideas(universe: str, strategy: str) -> List[TradeIdea]:
        return ideas

    def fake_vet(idea, rules):
        return IdeaVetting(
            score=scores[idea.symbol],
            verdict=VetVerdict.ACCEPT,
            rationale="",
            reasons=[],
        )

    opened = []

    def fake_open(idea, qty):
        opened.append(idea)
        return _dummy_position(idea, qty)

    result = run_open_cycle(
        universe="U",
        strategy="short_put_spread_index_45d",
        max_trades=2,
        min_score=0,
        idea_generator=fake_ideas,
        vet_one=fake_vet,
        open_from_idea=fake_open,
    )

    assert len(result.opened) == 2
    assert opened == [idea_high, idea_mid]


def test_open_cycle_no_eligible_trades_skips_open():
    ideas = [_make_idea("AAA"), _make_idea("BBB")]

    def fake_ideas(universe: str, strategy: str) -> List[TradeIdea]:
        return ideas

    def fake_vet(idea, rules):
        return IdeaVetting(score=10, verdict=VetVerdict.REJECT, rationale="", reasons=["reject"])

    opened = []

    def fake_open(idea, qty):
        opened.append(idea)
        return _dummy_position(idea, qty)

    result = run_open_cycle(
        universe="U",
        strategy="short_put_spread_index_45d",
        max_trades=3,
        min_score=50,
        idea_generator=fake_ideas,
        vet_one=fake_vet,
        open_from_idea=fake_open,
    )

    assert result.generated_count == 2
    assert result.eligible_count == 0
    assert len(result.opened) == 0
    assert opened == []
