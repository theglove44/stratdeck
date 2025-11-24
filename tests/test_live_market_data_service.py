from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from stratdeck.data.live_quotes import LiveMarketDataService, QuoteSnapshot


def test_snapshot_freshness():
    recent = QuoteSnapshot(
        symbol="SPX",
        bid=Decimal("10.0"),
        ask=Decimal("11.0"),
        mid=Decimal("10.5"),
        asof=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    stale = QuoteSnapshot(
        symbol="SPX",
        bid=Decimal("10.0"),
        ask=Decimal("11.0"),
        mid=Decimal("10.5"),
        asof=datetime.now(timezone.utc) - timedelta(seconds=10),
    )

    assert recent.is_fresh(timedelta(seconds=3))
    assert not stale.is_fresh(timedelta(seconds=3))


def test_handle_quote_event_updates_cache():
    svc = LiveMarketDataService(session=None, symbols=["SPX"])
    quote = SimpleNamespace(event_symbol="SPX", bid_price=4300.5, ask_price=4301.5)

    svc._handle_quote_event(quote)

    snap = svc.get_snapshot("SPX")
    assert snap is not None
    assert snap.bid == Decimal("4300.5")
    assert snap.ask == Decimal("4301.5")
    assert snap.mid == Decimal("4301.0")
    assert snap.is_fresh(svc.freshness_ttl)


def test_stale_snapshot_returns_none():
    svc = LiveMarketDataService(session=None, symbols=["SPX"])
    quote = SimpleNamespace(event_symbol="SPX", bid_price=100.0, ask_price=101.0)
    svc._handle_quote_event(quote)
    snap = svc.get_snapshot("SPX")
    assert snap is not None

    # Force staleness and ensure get_snapshot respects TTL
    snap.asof = datetime.now(timezone.utc) - timedelta(seconds=10)
    assert svc.get_snapshot("SPX") is None


def test_wait_for_snapshot_returns_when_available():
    svc = LiveMarketDataService(session=None, symbols=["SPX"])
    assert svc.wait_for_snapshot("SPX", timeout=0.05) is None

    quote = SimpleNamespace(event_symbol="SPX", bid_price=100.0, ask_price=101.0)
    svc._handle_quote_event(quote)

    snap = svc.wait_for_snapshot("SPX", timeout=0.05)
    assert snap is not None
    assert snap.symbol == "SPX"
