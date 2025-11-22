import pytest

from stratdeck.agents.trade_planner import TradePlanner
from stratdeck.strategy_engine import SymbolStrategyTask
from stratdeck.strategies import (
    ProductType,
    StrategyFilters,
    StrategyTemplate,
    UniverseConfig,
    UniverseSource,
    UniverseSourceType,
)


def _scan_row(symbol: str, low: float, high: float, ivr: float):
    return {
        "symbol": symbol,
        "ivr": ivr,
        "ta_directional_bias": "bullish",
        "ta_vol_bias": "normal",
        "strategy_hint": "short_premium_range",
        "ta": {
            "scores": {
                "directional_bias": "bullish",
                "vol_bias": "normal",
                "ta_bias": 0.0,
            },
            "structure": {
                "support": [low],
                "resistance": [high],
                "range": {
                    "low": low,
                    "high": high,
                    "in_range": True,
                    "position_in_range": 0.5,
                },
            },
            "trend_regime": {"state": "uptrend"},
            "vol_regime": {"state": "normal"},
        },
    }


class StubChains:
    def get_available_dtes(self, symbol: str):
        return []

    def price_structure(self, **kwargs):
        # Provide credit_per_width so filters can evaluate.
        return {"pop": 0.6, "credit_per_width": 0.3, "credit": 0.6}


def _task():
    source = UniverseSource(type=UniverseSourceType.STATIC, tickers=["SPX"])
    universe = UniverseConfig(
        name="index_core",
        product_type=ProductType.INDEX,
        source=source,
    )
    strategy = StrategyTemplate(
        name="short_put_spread_index_45d",
        applies_to_universes=["index_core"],
        filters=StrategyFilters(min_ivr=0.2, min_credit_per_width=0.25),
    )
    return SymbolStrategyTask(symbol="SPX", strategy=strategy, universe=universe)


def test_strategy_filters_gate_and_annotate(monkeypatch):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")
    planner = TradePlanner(chains_client=StubChains())
    task = _task()

    failing = _scan_row("SPX", 100.0, 110.0, ivr=0.1)
    passing = _scan_row("SPX", 100.0, 110.0, ivr=0.35)

    ideas = planner.generate_from_scan_results_with_strategies(
        scan_rows=[failing, passing],
        tasks=[task],
        dte_target=30,
        max_per_symbol=2,
    )

    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.filters_passed is True
    assert idea.filters_applied["min_ivr"] == pytest.approx(0.2)
    assert idea.filters_applied["min_credit_per_width"] == pytest.approx(0.25)
    assert idea.filter_reasons == []
    assert idea.strategy_id == "short_put_spread_index_45d"
    assert idea.universe_id == "index_core"
