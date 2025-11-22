import pytest

from stratdeck.tools.positions import PaperPosition, PaperPositionLeg, PositionsStore


def test_positions_store_roundtrip(tmp_path):
    store_path = tmp_path / ".stratdeck" / "positions.json"
    store = PositionsStore(store_path)
    assert store.list_positions() == []

    pos = PaperPosition(
        symbol="XSP",
        trade_symbol="XSP",
        strategy="short_put_spread",
        strategy_id="short_put_spread_index_45d",
        universe_id="index_core",
        direction="bullish",
        legs=[
            PaperPositionLeg(
                side="short",
                type="put",
                strike=100.0,
                expiry="2099-01-01",
                quantity=1,
                entry_mid=1.2,
            )
        ],
        qty=1,
        entry_mid=1.1,
        spread_width=5.0,
        dte=30,
    )

    stored = store.add_position(pos)
    assert store_path.exists()
    assert stored.entry_total == pytest.approx(110.0)

    reloaded = PositionsStore(store_path)
    loaded = reloaded.list_positions()
    assert len(loaded) == 1
    assert loaded[0].symbol == "XSP"
    assert loaded[0].status == "open"


def test_positions_store_filters_by_status(tmp_path):
    store_path = tmp_path / ".stratdeck" / "positions.json"
    store = PositionsStore(store_path)

    open_pos = PaperPosition(symbol="SPY", trade_symbol="SPY", strategy="short_put", qty=1, entry_mid=0.5)
    closed_pos = PaperPosition(symbol="QQQ", trade_symbol="QQQ", strategy="short_call", qty=1, entry_mid=0.75, status="closed")

    store.add_position(open_pos)
    store.add_position(closed_pos)

    open_only = store.list_positions(status="open")
    closed_only = store.list_positions(status="closed")

    assert len(open_only) == 1
    assert open_only[0].symbol == "SPY"
    assert len(closed_only) == 1
    assert closed_only[0].symbol == "QQQ"
