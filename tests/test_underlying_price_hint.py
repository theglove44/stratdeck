from typing import Dict, List

import pytest

from stratdeck.agents.trade_planner import (
    TradePlanner,
    resolve_underlying_price_hint,
)


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
        def get_quote(self, symbol: str):  # pragma: no cover - used via planner
            calls.append(symbol)
            return {"mid": None, "last": 640.0}

    monkeypatch.setattr("stratdeck.data.factory.get_provider", lambda: FakeProvider())

    planner = TradePlanner()
    ideas = planner.generate_from_scan_results([_scan_row("XSP", 630.0, 670.0)])
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.underlying_price_hint == pytest.approx(640.0)
    assert calls == ["XSP"]


def test_resolve_underlying_price_hint_prefers_live_over_ta(caplog):
    caplog.set_level("INFO")

    class FakeProvider:
        def get_quote(self, symbol: str):
            return {"mid": 123.45, "mark": 120.0, "last": 119.0}

    price = resolve_underlying_price_hint(
        symbol="SPX",
        data_symbol="^GSPC",
        provider=FakeProvider(),
        ta_price_hint=111.0,
    )

    assert price == pytest.approx(123.45)
    assert any("live quote used" in rec.message for rec in caplog.records)


def test_resolve_underlying_price_hint_uses_ta_when_provider_missing():
    price = resolve_underlying_price_hint(
        symbol="AAPL",
        data_symbol="AAPL",
        provider=None,
        ta_price_hint=150.5,
    )
    assert price == pytest.approx(150.5)


def test_resolve_underlying_price_hint_falls_back_to_ta_on_error():
    class FailingProvider:
        def get_quote(self, symbol: str):
            raise RuntimeError("boom")

    price = resolve_underlying_price_hint(
        symbol="AAPL",
        data_symbol="AAPL",
        provider=FailingProvider(),
        ta_price_hint=151.5,
    )
    assert price == pytest.approx(151.5)


def test_resolve_underlying_price_hint_handles_mark_and_last():
    class MarkOnlyProvider:
        def get_quote(self, symbol: str):
            return {"mid": None, "mark": 55.0, "last": 50.0}

    price_mark = resolve_underlying_price_hint(
        symbol="MSFT",
        data_symbol="MSFT",
        provider=MarkOnlyProvider(),
        ta_price_hint=None,
    )
    assert price_mark == pytest.approx(55.0)

    class LastOnlyProvider:
        def get_quote(self, symbol: str):
            return {"mid": None, "mark": None, "last": 44.0}

    price_last = resolve_underlying_price_hint(
        symbol="MSFT",
        data_symbol="MSFT",
        provider=LastOnlyProvider(),
        ta_price_hint=None,
    )
    assert price_last == pytest.approx(44.0)


def test_resolve_underlying_price_hint_spx_fallback_to_xsp(caplog):
    caplog.set_level("INFO")
    calls: List[str] = []

    class FakeProvider:
        def get_quote(self, symbol: str):
            calls.append(symbol)
            if symbol == "SPX":
                raise RuntimeError("rate limit")
            return {"mid": 42.0}

    price = resolve_underlying_price_hint(
        symbol="SPX",
        data_symbol="SPX",
        provider=FakeProvider(),
        ta_price_hint=None,
    )

    assert price == pytest.approx(420.0)
    assert calls == ["SPX", "XSP"]
    assert any("spx fallback via xsp" in rec.message for rec in caplog.records)


def test_resolve_underlying_price_hint_warns_when_live_and_ta_missing(caplog):
    caplog.set_level("WARNING")

    class EmptyProvider:
        def get_quote(self, symbol: str):
            return {"mid": None, "mark": None, "last": None}

    price = resolve_underlying_price_hint(
        symbol="QQQ",
        data_symbol="QQQ",
        provider=EmptyProvider(),
        ta_price_hint=None,
    )

    assert price == 0.0
    assert any("fallback missing live+ta" in rec.message for rec in caplog.records)
