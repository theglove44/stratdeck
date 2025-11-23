import json
from datetime import datetime, timedelta, timezone

from click.testing import CliRunner
import pytest

from stratdeck import cli
from stratdeck.tools.positions import PaperPosition, PaperPositionLeg, PositionsStore


class SingleProvider:
    def __init__(self, mid: float):
        self.mid = mid

    def get_option_chain(self, symbol: str, expiry: str = None):
        long_mid = 0.1
        return {
            "puts": [
                {"strike": 100.0, "mid": self.mid + long_mid},
                {"strike": 95.0, "mid": long_mid},
            ]
        }

    def get_quote(self, symbol: str):
        return {"mark": 380.0}

    def get_ivr(self, symbol: str):
        return 30.0


def test_positions_close_single(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    store_path = tmp_path / ".stratdeck" / "positions.json"
    store = PositionsStore(store_path)
    pos = store.add_position(
        PaperPosition(
            symbol="SPY",
            trade_symbol="SPY",
            strategy_id="short_put_spread_index_45d",
            universe_id="index_core",
            direction="bullish",
            legs=[
                PaperPositionLeg(side="short", type="put", strike=100.0, expiry="2100-01-01", quantity=1),
                PaperPositionLeg(side="long", type="put", strike=95.0, expiry="2100-01-01", quantity=1),
            ],
            qty=1,
            entry_mid=1.0,
            spread_width=5.0,
            opened_at=datetime.now(timezone.utc) - timedelta(days=5),
        )
    )

    provider = SingleProvider(mid=0.5)
    monkeypatch.setattr(cli, "get_provider", lambda: provider)
    monkeypatch.setattr(cli, "load_snapshot", lambda: {"SPY": 0.25})

    env = {"STRATDECK_TRADING_MODE": "paper", "STRATDECK_DATA_MODE": "mock"}

    dry = runner.invoke(
        cli.cli, ["positions", "close", "--id", str(pos.id), "--dry-run", "--json-output"], env=env
    )
    assert dry.exit_code == 0, dry.output
    dry_payload = json.loads(dry.output)
    assert dry_payload["dry_run"] is True
    open_after_dry = PositionsStore(store_path).get(pos.id)
    assert open_after_dry.status == "open"

    res = runner.invoke(
        cli.cli,
        ["positions", "close", "--id", str(pos.id), "--reason", "tester", "--json-output"],
        env=env,
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["dry_run"] is False
    reloaded = PositionsStore(store_path).get(pos.id)
    assert reloaded.status == "closed"
    assert reloaded.exit_mid == pytest.approx(provider.mid)
    assert reloaded.exit_reason == "tester"
    assert reloaded.realized_pl_total is not None
