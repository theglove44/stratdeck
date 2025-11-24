from types import SimpleNamespace

from stratdeck.data import factory


def test_build_live_quotes_subscribes_index_core(monkeypatch):
    captured = SimpleNamespace(init_symbols=None, start_called=False)

    class DummyService:
        def __init__(self, session, symbols, **kwargs):
            captured.init_symbols = list(symbols)

        def start(self):
            captured.start_called = True

        def stop(self):
            pass

    monkeypatch.setattr(factory, "make_tasty_streaming_session_from_env", lambda: object())
    monkeypatch.setattr(factory, "LiveMarketDataService", DummyService)
    monkeypatch.setattr(factory, "_live_quotes_instance", None)

    svc = factory._build_live_quotes()

    assert svc is not None
    assert set(captured.init_symbols or []) == {"SPX", "XSP"}
    assert captured.start_called is True
