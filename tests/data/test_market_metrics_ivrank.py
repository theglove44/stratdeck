from typing import Any, Dict, List, Optional

import pytest

from stratdeck.data.market_metrics import (
    DEFAULT_CHUNK_SIZE,
    fetch_market_metrics_raw,
    _extract_ivr_from_item,
    _items_from_response,
    fetch_iv_rank_for_symbols,
)


class FakeResponse:
    def __init__(self, payload: Any = None, status_code: int = 200, json_exc: Optional[Exception] = None):
        self._payload = payload
        self.status_code = status_code
        self._json_exc = json_exc

    def json(self):
        if self._json_exc:
            raise self._json_exc
        return self._payload


class FakeSession:
    def __init__(self, responses: List[FakeResponse]):
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def get(self, url: str, params=None, timeout: Optional[int] = None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if not self._responses:
            raise AssertionError("No response queued for FakeSession.get")
        return self._responses.pop(0)


def test_extract_ivr_prefers_implied_volatility_index_rank():
    item = {
        "symbol": "XSP",
        "implied-volatility-index-rank": "0.268717949",
        "tw-implied-volatility-index-rank": 0.40,
    }
    ivr = _extract_ivr_from_item(item)
    assert ivr == pytest.approx(0.268717949)


def test_extract_ivr_handles_string_fraction():
    item = {
        "implied-volatility-index-rank": "0.5",
    }
    ivr = _extract_ivr_from_item(item)
    assert ivr == pytest.approx(0.5)


def test_extract_ivr_falls_back_when_canonical_missing():
    item = {
        "tw-implied-volatility-index-rank": 0.32,
    }
    ivr = _extract_ivr_from_item(item)
    assert ivr == pytest.approx(0.32)


def test_extract_ivr_scales_percent_values():
    item = {
        "implied-volatility-index-rank": 26.8717949,
    }
    ivr = _extract_ivr_from_item(item)
    assert ivr == pytest.approx(0.268717949, rel=1e-6)


def test_extract_ivr_clamps_out_of_range():
    assert _extract_ivr_from_item({"implied-volatility-index-rank": 200}) == 1.0
    assert _extract_ivr_from_item({"implied-volatility-index-rank": -10}) == 0.0


def test_extract_falls_back_when_canonical_missing_percent():
    item = {"symbol": "GLD", "implied-volatility-index-rank": 88.2}
    assert _extract_ivr_from_item(item) == pytest.approx(0.882)


def test_items_from_response_handles_envelopes():
    payload = {"data": {"items": [{"symbol": "SPX"}, {"symbol": "AAPL"}]}}
    assert _items_from_response(payload) == [{"symbol": "SPX"}, {"symbol": "AAPL"}]

    payload2 = {"items": [{"symbol": "QQQ"}]}
    assert _items_from_response(payload2) == [{"symbol": "QQQ"}]

    assert _items_from_response({"items": "oops"}) == []
    assert _items_from_response("bad") == []


def test_fetch_iv_rank_for_symbols_happy_path_batch():
    responses = [
        FakeResponse(
            {
                "data": {
                    "items": [
                        {"symbol": "spx", "implied-volatility-index-rank": 0.3},
                        {"symbol": "aapl", "tw-implied-volatility-index-rank": 0.55},
                    ]
                }
            }
        )
    ]
    session = FakeSession(responses)

    result = fetch_iv_rank_for_symbols(["SPX", "aapl"], session=session, chunk_size=DEFAULT_CHUNK_SIZE)

    assert result == {"AAPL": 0.55, "SPX": 0.3}
    assert session.calls
    assert session.calls[0]["params"] == {"symbols": "AAPL,SPX"}


def test_fetch_iv_rank_for_symbols_chunks_and_skips_failures():
    responses = [
        FakeResponse(status_code=500),
        FakeResponse(
            {"items": [{"symbol": "MSFT", "implied-volatility-index-rank": 23}]}
        ),
        FakeResponse(json_exc=ValueError("no json")),
        FakeResponse(
            {"data": {"items": [{"symbol": "SPX", "implied-volatility-index-rank": 0.44}]}}
        ),
    ]
    session = FakeSession(responses)

    result = fetch_iv_rank_for_symbols(["SPX", "MSFT", "QQQ", "IWM"], session=session, chunk_size=2)

    assert result == {"MSFT": pytest.approx(0.23), "SPX": 0.44}
    # two attempts per chunk due to retry logic
    assert len(session.calls) == 4
    assert session.calls[0]["params"] == {"symbols": "IWM,MSFT"}
    assert session.calls[1]["params"] == {"symbols": "IWM,MSFT"}
    assert session.calls[2]["params"] == {"symbols": "QQQ,SPX"}
    assert session.calls[3]["params"] == {"symbols": "QQQ,SPX"}


def test_fetch_market_metrics_raw_returns_items():
    responses = [
        FakeResponse(
            {
                "data": {
                    "items": [
                        {"symbol": "SPX", "tw-implied-volatility-index-rank": 11},
                        {"symbol": "AAPL", "implied-volatility-index-rank": 9.9},
                    ]
                }
            }
        )
    ]
    session = FakeSession(responses)

    raw = fetch_market_metrics_raw(["spx", "AAPL"], session=session, chunk_size=DEFAULT_CHUNK_SIZE)

    assert raw == {
        "data": {
            "items": [
                {"symbol": "SPX", "tw-implied-volatility-index-rank": 11},
                {"symbol": "AAPL", "implied-volatility-index-rank": 9.9},
            ]
        }
    }
    assert session.calls
    assert session.calls[0]["params"] == {"symbols": "AAPL,SPX"}
    assert session.calls[0]["url"].endswith("/market-metrics")
