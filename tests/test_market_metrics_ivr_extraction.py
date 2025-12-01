import pytest

from stratdeck.data.market_metrics import _extract_ivr_from_item


def test_extract_ivr_from_item_canonical_fraction():
    item = {"symbol": "SPX", "implied-volatility-index-rank": 0.15}
    ivr = _extract_ivr_from_item(item)
    assert ivr == pytest.approx(0.15)


def test_extract_ivr_from_item_canonical_percent():
    item = {"symbol": "SPX", "implied-volatility-index-rank": 15.0}
    ivr = _extract_ivr_from_item(item)
    assert ivr == pytest.approx(0.15)


def test_extract_ivr_from_tos_field():
    item = {"symbol": "SPX", "tos-implied-volatility-index-rank": 27.0}
    ivr = _extract_ivr_from_item(item)
    assert ivr == pytest.approx(0.27)


def test_extract_ivr_clamps_high_values():
    item = {"symbol": "SPX", "implied-volatility-index-rank": 180.0}
    ivr = _extract_ivr_from_item(item)
    assert 0.99 <= ivr <= 1.0


def test_extract_ivr_clamps_negative_values():
    item = {"symbol": "SPX", "implied-volatility-index-rank": -5.0}
    ivr = _extract_ivr_from_item(item)
    assert ivr == 0.0


def test_extract_ivr_missing_field_returns_none():
    item = {"symbol": "SPX"}
    ivr = _extract_ivr_from_item(item)
    assert ivr is None


def test_extract_ivr_non_numeric_returns_none():
    item = {"symbol": "SPX", "implied-volatility-index-rank": "n/a"}
    ivr = _extract_ivr_from_item(item)
    assert ivr is None
