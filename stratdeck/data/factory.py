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

log = logging.getLogger(__name__)

_provider_instance: Optional[IDataProvider] = None
_live_quotes_instance: Optional[LiveMarketDataService] = None


def _stop_live_quotes() -> None:
    global _live_quotes_instance
    if _live_quotes_instance is not None:
        try:
            _live_quotes_instance.stop()
        except Exception:
            pass
        _live_quotes_instance = None


def _build_live_quotes() -> Optional[LiveMarketDataService]:
    global _live_quotes_instance
    session = make_tasty_streaming_session_from_env()
    if session is None:
        return None

    service = LiveMarketDataService(session=session, symbols=["SPX", "XSP"])
    try:
        service.start()
        _live_quotes_instance = service
        atexit.register(_stop_live_quotes)
        return service
    except Exception as exc:
        log.warning("DXLink streamer start failed: %r", exc)
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
