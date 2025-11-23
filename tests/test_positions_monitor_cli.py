import json
from datetime import datetime, timedelta, timezone

from click.testing import CliRunner

from stratdeck import cli
from stratdeck.tools.positions import PaperPosition, PaperPositionLeg, PositionsStore


class ProviderWithMid:
    def __init__(self, mid_by_symbol: dict[str, float], ivr: float = 25.0):
        self.mid_by_symbol = mid_by_symbol
        self.ivr = ivr

    def get_option_chain(self, symbol: str, expiry: str = None):
        desired = self.mid_by_symbol.get(symbol, 1.0)
        long_mid = 0.1
        short_mid = desired + long_mid
        return {
            "puts": [
                {"strike": 100.0, "mid": short_mid},
                {"strike": 95.0, "mid": long_mid},
            ]
        }

    def get_quote(self, symbol: str):
        return {"mark": 400.0}

    def get_ivr(self, symbol: str):
        return self.ivr


def test_positions_monitor_writes_snapshot(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    provider = ProviderWithMid({"XSP": 1.0}, ivr=30.0)
    monkeypatch.setattr(cli, "get_provider", lambda: provider)
    monkeypatch.setattr(cli, "load_snapshot", lambda: {"XSP": 0.3})

    store_path = tmp_path / ".stratdeck" / "positions.json"
    store = PositionsStore(store_path)
    store.add_position(
        PaperPosition(
            symbol="XSP",
            trade_symbol="XSP",
            strategy_id="short_put_spread_index_45d",
            universe_id="index_core",
            direction="bullish",
            legs=[
                PaperPositionLeg(side="short", type="put", strike=100.0, expiry="2100-01-01", quantity=1),
                PaperPositionLeg(side="long", type="put", strike=95.0, expiry="2100-01-01", quantity=1),
            ],
            qty=1,
            entry_mid=1.5,
            spread_width=5.0,
            opened_at=datetime.now(timezone.utc) - timedelta(days=5),
        )
    )

    env = {"STRATDECK_TRADING_MODE": "paper", "STRATDECK_DATA_MODE": "mock"}
    result = runner.invoke(cli.cli, ["positions", "monitor", "--json-output"], env=env)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload and isinstance(payload, list)
    item = payload[0]
    assert "metrics" in item and "decision" in item
    assert item["position"]["status"] == "open"

    snapshot_path = tmp_path / ".stratdeck" / "last_position_monitoring.json"
    assert snapshot_path.exists()
