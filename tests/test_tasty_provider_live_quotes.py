from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from stratdeck.data.live_quotes import QuoteSnapshot
from stratdeck.data.tasty_provider import TastyProvider


def _make_provider(monkeypatch, live_quotes):
    monkeypatch.setenv("TASTY_USER", "user")
    monkeypatch.setenv("TASTY_PASS", "pass")
    monkeypatch.setattr(TastyProvider, "_login", lambda self: None)
    monkeypatch.setattr(TastyProvider, "_fetch_default_account", lambda self: None)
    provider = TastyProvider(live_quotes=live_quotes)
    provider.session = SimpleNamespace()  # avoid accidental network use
    return provider


def test_get_quote_prefers_live_snapshot(monkeypatch):
    snapshot = QuoteSnapshot(
        symbol="SPX",
        bid=Decimal("10"),
        ask=Decimal("12"),
        mid=Decimal("11"),
        asof=datetime.now(timezone.utc),
    )

    class DummyLive:
        def __init__(self):
            self.calls = []

        def get_snapshot(self, symbol):
            self.calls.append(f"get:{symbol}")
            return snapshot

        def wait_for_snapshot(self, symbol, timeout=0.5):
            self.calls.append(f"wait:{symbol}")
            return snapshot

    live = DummyLive()
    provider = _make_provider(monkeypatch, live)
    monkeypatch.setattr(
        provider, "_get_quote_rest", lambda sym: (_ for _ in ()).throw(AssertionError("REST used"))
    )

    quote = provider.get_quote("spx")

    assert quote["source"] == "dxlink"
    assert quote["mid"] == pytest.approx(11.0)
    assert live.calls == ["get:SPX"]


def test_wait_for_snapshot_used_before_rest(monkeypatch):
    snapshot = QuoteSnapshot(
        symbol="SPY",
        bid=Decimal("1.5"),
        ask=Decimal("2.5"),
        mid=Decimal("2.0"),
        asof=datetime.now(timezone.utc),
    )

    class WaitingLive:
        def __init__(self):
            self.calls = []

        def get_snapshot(self, symbol):
            self.calls.append(f"get:{symbol}")
            return None

        def wait_for_snapshot(self, symbol, timeout=0.5):
            self.calls.append(f"wait:{symbol}")
            return snapshot

    live = WaitingLive()
    provider = _make_provider(monkeypatch, live)
    monkeypatch.setattr(
        provider, "_get_quote_rest", lambda sym: (_ for _ in ()).throw(AssertionError("REST used"))
    )

    quote = provider.get_quote("spy")

    assert quote["source"] == "dxlink"
    assert quote["mid"] == pytest.approx(2.0)
    assert live.calls == ["get:SPY", "wait:SPY"]


def test_rest_fallback_is_throttled(monkeypatch):
    class EmptyLive:
        def get_snapshot(self, symbol):
            return None

        def wait_for_snapshot(self, symbol, timeout=0.5):
            return None

    rest_calls = {}

    def fake_rest(self, symbol):
        rest_calls[symbol] = rest_calls.get(symbol, 0) + 1
        return {"symbol": symbol, "bid": 1.0, "ask": 3.0, "last": 2.5, "mark": 2.0, "mid": 2.0}

    monkeypatch.setattr(TastyProvider, "_get_quote_rest", fake_rest)
    provider = _make_provider(monkeypatch, EmptyLive())
    provider._quote_cache_ttl = 10.0
    fake_time = [0.0]
    provider._now = lambda: fake_time[0]

    q1 = provider.get_quote("AAPL")
    q2 = provider.get_quote("AAPL")
    fake_time[0] += 11.0
    q3 = provider.get_quote("AAPL")

    assert rest_calls["AAPL"] == 2
    assert q1["source"] == "rest-fallback"
    assert q2["mid"] == pytest.approx(2.0)
    assert q3["mid"] == pytest.approx(2.0)
