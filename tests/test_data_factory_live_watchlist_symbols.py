from stratdeck.data import factory
from stratdeck.strategies import (
    ProductType,
    StrategyConfig,
    UniverseConfig,
    UniverseSource,
    UniverseSourceType,
)


def test_resolve_live_symbols_unions_index_and_watchlist(monkeypatch):
    universes = {
        "index_core": UniverseConfig(
            name="index_core",
            product_type=ProductType.INDEX,
            source=UniverseSource(type=UniverseSourceType.STATIC, tickers=["SPX", "XSP"]),
        ),
        "tasty_watchlist_stratdeck": UniverseConfig(
            name="tasty_watchlist_stratdeck",
            product_type=ProductType.ANY,
            source=UniverseSource(
                type=UniverseSourceType.TASTY_WATCHLIST,
                watchlist_name="StratDeckUniverse",
            ),
        ),
    }

    cfg = StrategyConfig(universes=universes, strategies=[])

    monkeypatch.setattr(factory, "load_strategy_config", lambda: cfg)
    monkeypatch.setattr(factory, "get_watchlist_symbols", lambda name: ["MSFT", "AAPL"])

    symbols = factory._resolve_live_symbols()

    assert symbols == ["AAPL", "MSFT", "SPX", "XSP"]

