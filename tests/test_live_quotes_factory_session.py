import sys
from types import SimpleNamespace

import stratdeck.data.factory as factory
from stratdeck.data import live_quotes


def test_make_tasty_streaming_session_from_env(monkeypatch):
    class DummySession:
        def __init__(self, client_secret, refresh_token, is_test=False):
            self.client_secret = client_secret
            self.refresh_token = refresh_token
            self.is_test = is_test

    monkeypatch.setenv("TASTY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("TASTY_REFRESH_TOKEN", "refresh")
    monkeypatch.setenv("TASTY_IS_TEST", "1")
    monkeypatch.setitem(sys.modules, "tastytrade", SimpleNamespace(Session=DummySession))

    session = live_quotes.make_tasty_streaming_session_from_env()

    assert isinstance(session, DummySession)
    assert session.client_secret == "secret"
    assert session.refresh_token == "refresh"
    assert session.is_test is True


def test_build_live_quotes_uses_streaming_helper(monkeypatch):
    sentinel_session = object()
    created = {}
    sentinel_symbols = ["SPX", "XSP", "AAPL"]

    class DummyService:
        def __init__(self, session, symbols):
            created["session"] = session
            created["symbols"] = symbols
            self.started = False

        def start(self):
            self.started = True
            created["started"] = True

    monkeypatch.setattr(factory, "LiveMarketDataService", DummyService)
    monkeypatch.setattr(
        factory, "make_tasty_streaming_session_from_env", lambda: sentinel_session
    )
    monkeypatch.setattr(factory, "_resolve_live_symbols", lambda: sentinel_symbols)
    monkeypatch.setattr(factory.atexit, "register", lambda func: None)

    factory._live_quotes_instance = None
    service = factory._build_live_quotes()

    assert isinstance(service, DummyService)
    assert created["session"] is sentinel_session
    assert created["symbols"] == sentinel_symbols
    assert created["started"] is True
    assert factory._live_quotes_instance is service

    factory._live_quotes_instance = None
