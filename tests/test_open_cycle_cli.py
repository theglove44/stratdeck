import json
from datetime import datetime, timezone

from click.testing import CliRunner

from stratdeck import cli
from stratdeck.agents.trade_planner import TradeIdea, TradeLeg
from stratdeck.orchestrator import OpenCycleResult, OpenedPositionSummary
from stratdeck.tools.positions import PaperPosition
from stratdeck.vetting import IdeaVetting, VetVerdict


def _sample_result() -> OpenCycleResult:
    leg = TradeLeg(side="short", type="put", strike=100.0, expiry="2024-01-19", quantity=1)
    idea = TradeIdea(
        symbol="SPX",
        data_symbol="SPX",
        trade_symbol="SPX",
        strategy="short_put_spread",
        direction="bullish",
        vol_context="normal",
        rationale="sample",
        legs=[leg],
        strategy_id="short_put_spread_index_45d",
        universe_id="index_core",
    )
    vet = IdeaVetting(
        score=92.0,
        verdict=VetVerdict.ACCEPT,
        rationale="passes",
        reasons=["ok"],
    )
    pos = PaperPosition(
        symbol="SPX",
        trade_symbol="SPX",
        strategy="short_put_spread",
        strategy_id="short_put_spread_index_45d",
        entry_mid=1.25,
        qty=1,
        legs=[],
        opened_at=datetime.now(timezone.utc),
    )
    opened = OpenedPositionSummary(
        idea=idea,
        vetting=vet,
        position=pos,
        opened_at=datetime.now(timezone.utc),
    )
    return OpenCycleResult(
        universe="index_core",
        strategy="short_put_spread_index_45d",
        generated_count=3,
        eligible_count=2,
        opened=[opened],
    )


def test_open_cycle_cli_human(monkeypatch):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")
    sample = _sample_result()
    monkeypatch.setattr(cli, "run_open_cycle", lambda **kwargs: sample)

    runner = CliRunner()
    result = runner.invoke(
        cli.cli,
        [
            "open-cycle",
            "--universe",
            "index_core",
            "--strategy",
            "short_put_spread_index_45d",
            "--max-trades",
            "1",
            "--min-score",
            "0",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "[open-cycle]" in result.output
    assert "opened=1" in result.output
    assert "SPX" in result.output


def test_open_cycle_cli_json(monkeypatch):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")
    sample = _sample_result()
    monkeypatch.setattr(cli, "run_open_cycle", lambda **kwargs: sample)

    runner = CliRunner()
    result = runner.invoke(
        cli.cli,
        [
            "open-cycle",
            "--universe",
            "index_core",
            "--strategy",
            "short_put_spread_index_45d",
            "--max-trades",
            "1",
            "--min-score",
            "0",
            "--json-output",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data, "expected payload to include at least one entry"
    item = data[0]
    assert "idea" in item
    assert "vetting" in item
    assert "position" in item
    assert "opened_at" in item
