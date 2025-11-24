# stratdeck/data/factory.py
import atexit
import logging
import os
from typing import Optional

from .provider import IDataProvider
from .mock_provider import MockProvider
from .tasty_provider import TastyProvider
from .live_quotes import (
    LiveMarketDataService,
    make_tasty_streaming_session_from_env,
)
from stratdeck.strategy_engine import resolve_universe_tickers
from stratdeck.strategies import load_strategy_config

log = logging.getLogger(__name__)

_provider_instance: Optional[IDataProvider] = None
_live_quotes_instance: Optional[LiveMarketDataService] = None


def _resolve_live_symbols_for_index_core() -> list[str]:
    """
    Determine which symbols should be subscribed on DXLink.

    Falls back to the index_core universe (SPX/XSP) if config resolution fails.
    """

    fallback = ["SPX", "XSP"]
    try:
        cfg = load_strategy_config()
        universe = cfg.universes.get("index_core")
        if universe is None:
            return fallback
        symbols = resolve_universe_tickers(universe=universe)
        return symbols or fallback
    except Exception as exc:
        log.warning("Falling back to default live symbols: %r", exc)
        return fallback


def _build_live_quotes() -> Optional[LiveMarketDataService]:
    global _live_quotes_instance
    if _live_quotes_instance is not None:
        return _live_quotes_instance

    try:
        session = make_tasty_streaming_session_from_env()

        symbols = _resolve_live_symbols_for_index_core()
        service = LiveMarketDataService(session, symbols)
        service.start()

        if hasattr(service, "stop"):
            atexit.register(service.stop)

        _live_quotes_instance = service
        return service
    except Exception as exc:
        log.warning("DXLink streamer start failed: %r", exc)
        _live_quotes_instance = None
        return None

def get_provider() -> IDataProvider:
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance
    mode = os.getenv("STRATDECK_DATA_MODE", "mock").lower()
    if mode == "live":
        live_quotes = _build_live_quotes()
        _provider_instance = TastyProvider(live_quotes=live_quotes)
    else:
        _provider_instance = MockProvider()
    return _provider_instance
