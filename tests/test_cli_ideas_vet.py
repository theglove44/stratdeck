import json

from click.testing import CliRunner

from stratdeck import cli
from stratdeck.agents.trade_planner import TradeIdea, TradeLeg


def _sample_payload():
    short_leg = TradeLeg(side="short", type="put", strike=100.0, expiry="2025-01-17", quantity=1, delta=0.30, dte=45)
    long_leg = TradeLeg(side="long", type="put", strike=95.0, expiry="2025-01-17", quantity=1, delta=0.05, dte=45)
    idea = TradeIdea(
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
        ivr=0.32,
        pop=0.66,
        credit_per_width=0.30,
        short_put_delta=0.30,
        strategy_id="short_put_spread_index_45d",
    )
    return idea.to_dict()


def _write_ideas_file(tmp_path):
    payload = [_sample_payload()]
    path = tmp_path / "ideas.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_ideas_vet_human_mode(tmp_path):
    ideas_path = _write_ideas_file(tmp_path)
    runner = CliRunner()
    env = {"STRATDECK_DATA_MODE": "mock"}

    result = runner.invoke(cli.cli, ["ideas-vet", "--ideas-path", str(ideas_path)], env=env)

    assert result.exit_code == 0, result.output
    assert "verdict" in result.output.lower()
    assert "->" in result.output


def test_ideas_vet_json_mode(tmp_path):
    ideas_path = _write_ideas_file(tmp_path)
    runner = CliRunner()
    env = {"STRATDECK_DATA_MODE": "mock"}

    result = runner.invoke(
        cli.cli,
        ["ideas-vet", "--ideas-path", str(ideas_path), "--json-output"],
        env=env,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert payload
    vetting = payload[0].get("vetting")
    assert vetting is not None
    for key in ("score", "verdict", "rationale", "reasons"):
        assert key in vetting
    assert vetting["verdict"] == "ACCEPT"
    assert any("regime" in r for r in vetting["reasons"])
