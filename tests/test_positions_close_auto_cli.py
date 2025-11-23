import json
from datetime import datetime, timedelta, timezone

from click.testing import CliRunner

from stratdeck import cli
from stratdeck.tools.positions import PaperPosition, PaperPositionLeg, PositionsStore


class MultiSymbolProvider:
    def __init__(self, mid_map: dict[str, float], ivr: float = 30.0):
        self.mid_map = mid_map
        self.ivr = ivr

    def get_option_chain(self, symbol: str, expiry: str = None):
        desired = self.mid_map.get(symbol, 1.0)
        long_mid = 0.1
        return {
            "puts": [
                {"strike": 100.0, "mid": desired + long_mid},
                {"strike": 95.0, "mid": long_mid},
            ]
        }

    def get_quote(self, symbol: str):
        return {"mark": 390.0}

    def get_ivr(self, symbol: str):
        return self.ivr


def _add_position(store: PositionsStore, symbol: str, entry_mid: float, expiry: str):
    store.add_position(
        PaperPosition(
            symbol=symbol,
            trade_symbol=symbol,
            strategy_id="short_put_spread_index_45d",
            universe_id="index_core",
            direction="bullish",
            legs=[
                PaperPositionLeg(side="short", type="put", strike=100.0, expiry=expiry, quantity=1),
                PaperPositionLeg(side="long", type="put", strike=95.0, expiry=expiry, quantity=1),
            ],
            qty=1,
            entry_mid=entry_mid,
            spread_width=5.0,
            opened_at=datetime.now(timezone.utc) - timedelta(days=5),
        )
    )


def test_positions_close_auto_dry_run_and_close(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    store_path = tmp_path / ".stratdeck" / "positions.json"
    store = PositionsStore(store_path)

    _add_position(store, "WIN", entry_mid=1.0, expiry="2100-01-01")
    _add_position(store, "DTE", entry_mid=1.0, expiry=(datetime.now(timezone.utc) + timedelta(days=10)).date().isoformat())
    _add_position(store, "HOLD", entry_mid=1.0, expiry="2100-01-01")

    provider = MultiSymbolProvider({"WIN": 0.2, "DTE": 1.0, "HOLD": 0.9}, ivr=35.0)
    monkeypatch.setattr(cli, "get_provider", lambda: provider)
    monkeypatch.setattr(cli, "load_snapshot", lambda: {"WIN": 0.3, "DTE": 0.3, "HOLD": 0.3})

    env = {"STRATDECK_TRADING_MODE": "paper", "STRATDECK_DATA_MODE": "mock"}

    # Dry run should not persist changes.
    dry_result = runner.invoke(cli.cli, ["positions", "close-auto", "--dry-run", "--json-output"], env=env)
    assert dry_result.exit_code == 0, dry_result.output
    dry_payload = json.loads(dry_result.output)
    assert len(dry_payload) == 2  # WIN (profit target) + DTE (time exit)
    reloaded = PositionsStore(store_path)
    assert all(p.status == "open" for p in reloaded.list_positions())

    result = runner.invoke(cli.cli, ["positions", "close-auto", "--json-output"], env=env)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 2

    final = PositionsStore(store_path)
    closed = [p for p in final.list_positions(status="closed")]
    assert len(closed) == 2
    reasons = {p.exit_reason for p in closed}
    assert "TARGET_PROFIT_HIT" in reasons
    assert "DTE_BELOW_THRESHOLD" in reasons

    open_positions = final.list_positions(status="open")
    assert len(open_positions) == 1
    hold = final.get(open_positions[0].id)
    assert hold.symbol == "HOLD"
