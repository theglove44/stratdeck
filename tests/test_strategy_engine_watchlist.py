from stratdeck.strategies import (
    StrategyConfig,
    StrategyTemplate,
    UniverseConfig,
    UniverseSource,
    UniverseSourceType,
)
from stratdeck.strategy_engine import (
    build_strategy_universe_assignments,
    resolve_universe_tickers,
)


def test_resolve_universe_tasty_watchlist_uses_resolver():
    universe = UniverseConfig(
        name="tasty_watchlist_stratdeck",
        source=UniverseSource(
            type=UniverseSourceType.TASTY_WATCHLIST,
            watchlist_name="StratDeckUniverse",
            max_symbols=1,
        ),
    )

    captured = {}

    def resolver(name: str, max_symbols):
        captured["name"] = name
        captured["max"] = max_symbols
        return ["msft", "aapl"]

    symbols = resolve_universe_tickers(universe=universe, tasty_watchlist_resolver=resolver)

    assert symbols == ["MSFT", "AAPL"]
    assert captured == {"name": "StratDeckUniverse", "max": 1}


def test_build_assignments_includes_watchlist_symbols(monkeypatch):
    universes = {
        "index_core": UniverseConfig(
            name="index_core",
            source=UniverseSource(type=UniverseSourceType.STATIC, tickers=["SPX", "XSP"]),
        ),
        "tasty_watchlist_stratdeck": UniverseConfig(
            name="tasty_watchlist_stratdeck",
            source=UniverseSource(
                type=UniverseSourceType.TASTY_WATCHLIST,
                watchlist_name="StratDeckUniverse",
            ),
        ),
    }

    strategies = [
        StrategyTemplate(
            name="short_put_spread_equity_45d",
            applies_to_universes=["tasty_watchlist_stratdeck"],
        )
    ]

    cfg = StrategyConfig(universes=universes, strategies=strategies)

    assignments = build_strategy_universe_assignments(
        cfg=cfg,
        tasty_watchlist_resolver=lambda name, max_symbols: ["AAPL", "MSFT"],
    )

    assert assignments
    assert assignments[0].universe.name == "tasty_watchlist_stratdeck"
    assert assignments[0].symbols == ["AAPL", "MSFT"]

