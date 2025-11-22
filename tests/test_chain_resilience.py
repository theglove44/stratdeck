import pytest

from stratdeck.tools import chains


def test_get_chain_retries_rate_limit(monkeypatch):
    monkeypatch.setattr("stratdeck.tools.retries.time.sleep", lambda *_: None)

    class RateLimitError(Exception):
        status_code = 429

    class Provider:
        def __init__(self):
            self.calls = 0

        def get_option_chain(self, symbol, expiry=None):
            self.calls += 1
            if self.calls < 3:
                raise RateLimitError("Too Many Requests")
            return {"symbol": symbol, "expiry": expiry, "puts": [], "calls": []}

    prev = chains._provider
    provider = Provider()
    chains.set_provider(provider)
    try:
        data = chains.get_chain("SPX", expiry="2024-01-01")
    finally:
        chains.set_provider(prev)

    assert data.get("symbol") == "SPX"
    assert provider.calls == 3


def test_get_chain_returns_empty_after_retry_exhaustion(monkeypatch):
    monkeypatch.setattr("stratdeck.tools.retries.time.sleep", lambda *_: None)

    class RateLimitError(Exception):
        status_code = 429

    class Provider:
        def __init__(self):
            self.calls = 0

        def get_option_chain(self, symbol, expiry=None):
            self.calls += 1
            raise RateLimitError("429 Too Many Requests")

    prev = chains._provider
    provider = Provider()
    chains.set_provider(provider)
    try:
        data = chains.get_chain("QQQ", expiry="2024-01-01")
    finally:
        chains.set_provider(prev)

    assert data == {}
    assert provider.calls >= 1
