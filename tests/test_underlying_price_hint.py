from typing import Dict, List

import pytest

from stratdeck.agents.trade_planner import TradePlanner


def _scan_row(symbol: str, low: float, high: float) -> Dict:
    support: List[float] = [low]
    resistance: List[float] = [high]
    return {
        "symbol": symbol,
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
                "support": support,
                "resistance": resistance,
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


def test_underlying_hint_uses_ta_in_mock_mode(monkeypatch):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")
    planner = TradePlanner()
    ideas = planner.generate_from_scan_results([_scan_row("SPY", 100.0, 110.0)])
    assert len(ideas) == 1
    assert ideas[0].underlying_price_hint == pytest.approx(105.0)


def test_underlying_hint_live_prefers_mid_quote(monkeypatch):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "live")
    calls: List[str] = []

    class FakeProvider:
        def get_quote(self, symbol: str):
            calls.append(symbol)
            return {"mid": 4321.0, "last": 4300.0}

    monkeypatch.setattr("stratdeck.data.factory.get_provider", lambda: FakeProvider())

    planner = TradePlanner()
    ideas = planner.generate_from_scan_results([_scan_row("SPX", 4300.0, 4400.0)])
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.underlying_price_hint == pytest.approx(4321.0)
    assert calls == ["SPX"]


def test_underlying_hint_live_falls_back_to_last(monkeypatch):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "live")
    calls: List[str] = []

    class FakeProvider:
        def get_quote(self, symbol: str):
            calls.append(symbol)
            return {"mid": None, "last": 640.0}

    monkeypatch.setattr("stratdeck.data.factory.get_provider", lambda: FakeProvider())

    planner = TradePlanner()
    ideas = planner.generate_from_scan_results([_scan_row("XSP", 630.0, 670.0)])
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.underlying_price_hint == pytest.approx(640.0)
    assert calls == ["XSP"]
