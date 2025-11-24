from stratdeck.data import tasty_watchlists


def test_get_watchlist_symbols_normalizes_and_sorts(monkeypatch):
    payload = {
        "data": {
            "items": [
                {
                    "name": "StratDeckUniverse",
                    "items": [
                        {"symbol": "aapl", "instrument-type": "Equity"},
                        {"symbol": "MSFT", "instrument-type": "Equity"},
                        {
                            "symbol": "SPX  231215P03500000",
                            "instrument-type": "Equity Option",
                            "underlying-symbol": "SPX",
                        },
                        {"symbol": "GLD", "instrument-type": "ETF"},
                        {"symbol": "AAPL", "instrument-type": "Equity"},
                    ],
                }
            ]
        }
    }

    captured_urls = []

    class DummyResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.text = "json"

        def json(self):
            return self._payload

    class DummySession:
        def __init__(self, payload):
            self.payload = payload

        def get(self, url, timeout=30):
            captured_urls.append(url)
            return DummyResponse(self.payload)

    monkeypatch.setattr(
        tasty_watchlists, "make_tasty_session_from_env", lambda: DummySession(payload)
    )

    symbols = tasty_watchlists.get_watchlist_symbols("StratDeckUniverse")

    assert symbols == ["AAPL", "GLD", "MSFT", "SPX"]
    assert captured_urls == [f"{tasty_watchlists.API_BASE}/watchlists"]

