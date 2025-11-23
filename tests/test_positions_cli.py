import json

import pytest
from click.testing import CliRunner

from stratdeck import cli
from stratdeck.tools import orders
from stratdeck.tools.positions import PaperPosition, PositionsStore


class FakePricingAdapter:
    def __init__(self):
        self.calls = []

    def price_structure(self, symbol, strategy_type, legs, dte_target, target_delta_hint=None):
        self.calls.append(
            {
                "symbol": symbol,
                "strategy": strategy_type,
                "dte_target": dte_target,
                "target_delta": target_delta_hint,
                "legs": legs,
            }
        )
        return {
            "credit": 1.5,
            "credit_per_width": 0.3,
            "pop": 0.65,
            "width": 5.0,
            "legs": {
                "short": {"mid": 2.0, "strike": 100.0, "type": "put", "side": "short", "expiry": "2099-01-01"},
                "long": {"mid": 0.5, "strike": 95.0, "type": "put", "side": "long", "expiry": "2099-01-01"},
            },
            "expiry": "2099-01-01",
        }


def test_enter_auto_creates_position(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    last_path = tmp_path / ".stratdeck" / "last_trade_ideas.json"
    last_path.parent.mkdir(parents=True, exist_ok=True)
    idea = {
        "symbol": "XSP",
        "data_symbol": "XSP",
        "trade_symbol": "XSP",
        "strategy": "short_put_spread",
        "strategy_id": "short_put_spread_index_45d",
        "universe_id": "index_core",
        "direction": "bullish",
        "legs": [
            {"side": "short", "type": "put", "strike": 100.0, "expiry": "2099-01-01", "quantity": 1},
            {"side": "long", "type": "put", "strike": 95.0, "expiry": "2099-01-01", "quantity": 1},
        ],
        "spread_width": 5.0,
        "dte_target": 10,
        "pop": 0.65,
        "credit_per_width": 0.3,
        "estimated_credit": 1.5,
    }
    last_path.write_text(json.dumps([idea]), encoding="utf-8")

    fake_pricing = FakePricingAdapter()
    monkeypatch.setattr(orders, "ChainPricingAdapter", lambda: fake_pricing)

    env = {"STRATDECK_TRADING_MODE": "paper", "STRATDECK_DATA_MODE": "mock"}
    result = runner.invoke(
        cli.cli,
        ["enter-auto", "--qty", "1", "--confirm", "--json-output"],
        env=env,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["symbol"] == "XSP"
    assert payload["strategy_id"] == "short_put_spread_index_45d"
    assert payload["status"] == "open"
    assert payload.get("expiry")
    assert fake_pricing.calls, "pricing adapter should be invoked"

    positions_file = tmp_path / ".stratdeck" / "positions.json"
    saved = json.loads(positions_file.read_text())
    assert len(saved) == 1
    assert saved[0]["entry_mid"] == pytest.approx(1.5)
    assert saved[0]["status"] == "open"
    assert saved[0]["expiry"]
    assert saved[0]["dte"] is not None


def test_enter_auto_requires_last_trade_ideas(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.cli, ["enter-auto", "--confirm"], env={"STRATDECK_DATA_MODE": "mock"})
    assert result.exit_code != 0
    assert "No ideas file" in result.output or "trade-ideas" in result.output
    positions_file = tmp_path / ".stratdeck" / "positions.json"
    assert not positions_file.exists()


def test_positions_list_json_output(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    store = PositionsStore(tmp_path / ".stratdeck" / "positions.json")
    store.add_position(PaperPosition(symbol="SPY", trade_symbol="SPY", strategy="short_put", qty=1, entry_mid=1.0))

    result = runner.invoke(cli.cli, ["positions", "list", "--json-output"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert payload[0]["symbol"] == "SPY"


def test_positions_list_json_output_empty(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.cli, ["positions", "list", "--json-output"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == []
