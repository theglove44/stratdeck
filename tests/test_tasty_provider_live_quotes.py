# tests/test_tasty_provider_live_quotes.py

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from stratdeck.data.live_quotes import QuoteSnapshot
from stratdeck.data.tasty_provider import TastyProvider


class DummyLiveQuotes:
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.requested_symbols = []

    def get_snapshot(self, symbol: str):
        self.requested_symbols.append(symbol)
        return self._snapshot


def test_quote_from_snapshot_happy_path(monkeypatch):
    now = datetime.now(timezone.utc)
    snapshot = QuoteSnapshot(
        symbol="SPX",
        bid=Decimal("4999.0"),
        ask=Decimal("5001.0"),
        mid=Decimal("5000.0"),
        asof=now,
    )

    live = DummyLiveQuotes(snapshot)

    # We don't want TastyProvider.__init__ to actually log in for this test.
    # Easiest is to bypass it with __new__ and manually set attributes we need.
    provider = TastyProvider.__new__(TastyProvider)
    provider._live_quotes = live

    # Provide the helpers used inside _quote_from_snapshot
    provider._safe_float = lambda v: float(v) if v is not None else None
    provider._mid = lambda bid, ask, mark, last: (
        (bid + ask) / 2 if bid is not None and ask is not None else None
    )

    quote = provider._quote_from_snapshot("SPX")

    assert quote is not None
    assert quote["symbol"] == "SPX"
    assert quote["bid"] == 4999.0
    assert quote["ask"] == 5001.0
    assert quote["mid"] == 5000.0
    assert quote["last"] == 5000.0  # because mid is available
    assert quote["source"] == "dxlink"
    assert live.requested_symbols == ["SPX"]


def test_quote_from_snapshot_no_live_service():
    provider = TastyProvider.__new__(TastyProvider)
    provider._live_quotes = None

    quote = provider._quote_from_snapshot("SPX")
    assert quote is None


def test_quote_from_snapshot_no_snapshot(monkeypatch):
    class EmptyLiveQuotes:
        def get_snapshot(self, symbol: str):
            return None

    provider = TastyProvider.__new__(TastyProvider)
    provider._live_quotes = EmptyLiveQuotes()

    quote = provider._quote_from_snapshot("SPX")
    assert quote is None
    
def test_get_quote_prefers_dxlink_over_rest(monkeypatch):
    # Build a snapshot and fake live service
    from stratdeck.data.live_quotes import QuoteSnapshot

    snap = QuoteSnapshot(
        symbol="SPX",
        bid=Decimal("4999.0"),
        ask=Decimal("5001.0"),
        mid=Decimal("5000.0"),
        asof=datetime.now(timezone.utc),
    )

    class FakeLiveQuotes:
        def __init__(self, snapshot):
            self.snapshot = snapshot
        def get_snapshot(self, symbol):
            return self.snapshot

    provider = TastyProvider.__new__(TastyProvider)
    provider._live_quotes = FakeLiveQuotes(snap)
    provider._safe_float = lambda v: float(v) if v is not None else None
    provider._mid = lambda bid, ask, mark, last: (
        (bid + ask) / 2 if bid is not None and ask is not None else None
    )

    # If this gets called, the test should fail
    def fake_rest(_symbol: str):
        raise AssertionError("REST should not be called when DXLink snapshot exists")

    # Monkeypatch the instance method
    provider._get_quote_rest = fake_rest

    quote = provider.get_quote("spx")  # lower-case on purpose to test upper()

    assert quote["symbol"] == "SPX"  # uppercased
    assert quote["source"] == "dxlink"
    assert quote["mid"] == 5000.0


def test_get_quote_uses_rest_when_no_snapshot(monkeypatch):
    class FakeLiveQuotes:
        def get_snapshot(self, symbol):
            return None

    provider = TastyProvider.__new__(TastyProvider)
    provider._live_quotes = FakeLiveQuotes()

    called = {"symbol": None}

    def fake_get_quote_rest(symbol: str):
        called["symbol"] = symbol
        return {
            "symbol": symbol,
            "bid": 1.0,
            "ask": 2.0,
            "last": 1.5,
            "mark": 1.5,
            "mid": 1.5,
        }

    # stub out REST so we don't hit the network
    provider._get_quote_rest = fake_get_quote_rest

    # this is where quote is defined
    quote = provider.get_quote("MSFT")

    # now the asserts
    assert called["symbol"] == "MSFT"    # REST path used
    assert quote["symbol"] == "MSFT"
    assert quote["mid"] == 1.5
    assert "source" not in quote         # REST path shouldn't tag source


    def fake_rest(symbol: str):
        called["symbol"] = symbol
        return {
            "symbol": symbol,
            "bid": 1.0,
            "ask": 2.0,
            "last": 1.5,
            "mark": 1.5,
            "mid": 1.5,
        }

    provider._get_quote_rest = fake_rest

    quote = provider.get_quote("MSFT")

    assert called["symbol"] == "MSFT"    # REST path used
    assert quote["symbol"] == "MSFT"
    assert quote["mid"] == 1.5
    assert "source" not in quote         # REST path doesn't set source
def test_get_quote_rest_maps_fields_correctly(monkeypatch):
    provider = TastyProvider.__new__(TastyProvider)
    provider.INDEX_SYMBOLS = {"SPX"}  # make sure this branch is hit

    # Fake _get_json to avoid real HTTP
    def fake_get_json(path: str):
        assert path == "/market-data/Index/SPX"
        return {
            "data": {
                "bid": "100.0",
                "ask": "102.0",
                "last": "101.0",
                "mark": "101.0",
            }
        }

    provider._get_json = fake_get_json
    provider._mid = lambda bid, ask, mark, last: (
        mark if mark is not None else ((bid + ask) / 2 if bid and ask else last)
    )

    quote = provider._get_quote_rest("SPX")

    assert quote["symbol"] == "SPX"
    assert quote["bid"] == 100.0
    assert quote["ask"] == 102.0
    assert quote["last"] == 101.0
    assert quote["mid"] == 101.0
    assert quote["mark"] == 101.0


def test_get_quote_rest_uses_equity_path_for_non_index(monkeypatch):
    provider = TastyProvider.__new__(TastyProvider)
    provider.INDEX_SYMBOLS = {"SPX"}  # MSFT not in here

    paths = []

    def fake_get_json(path: str):
        paths.append(path)
        return {"data": {"bid": "10.0", "ask": "11.0"}}

    provider._get_json = fake_get_json
    provider._mid = lambda bid, ask, mark, last: (bid + ask) / 2

    quote = provider._get_quote_rest("MSFT")

    assert paths == ["/market-data/Equity/MSFT"]
    assert quote["symbol"] == "MSFT"
    assert quote["mid"] == 10.5
