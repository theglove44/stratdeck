# stratdeck/data/factory.py
import atexit
import logging
import os
from typing import Optional

from ..strategies import UniverseSourceType, load_strategy_config
from ..strategy_engine import resolve_universe_tickers
from .provider import IDataProvider
from .mock_provider import MockProvider
from .tasty_provider import TastyProvider
from .live_quotes import (
    LiveMarketDataService,
    make_tasty_streaming_session_from_env,
)
from .tasty_watchlists import get_watchlist_symbols

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


def _resolve_live_symbols() -> list[str]:
    """Resolve symbols for DXLink streaming.

    Includes the index_core universe plus any configured tasty_watchlist universes.
    Falls back to a minimal index set on failure.
    """

    try:
        cfg = load_strategy_config()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Failed to load strategy config for live symbols: %r", exc)
        return ["SPX", "XSP"]

    resolved_watchlists: dict[str, list[str]] = {}

    def tasty_watchlist_resolver(name: str, max_symbols: Optional[int]) -> list[str]:
        symbols = resolved_watchlists.get(name)
        if symbols is None:
            symbols = get_watchlist_symbols(name)
            resolved_watchlists[name] = symbols
        return symbols[:max_symbols] if max_symbols is not None else symbols

    symbols: set[str] = set()

    def _add_from_universe(universe_name: str) -> None:
        universe = cfg.universes.get(universe_name)
        if universe is None:
            return
        try:
            resolved = resolve_universe_tickers(
                universe=universe,
                tasty_watchlist_resolver=tasty_watchlist_resolver,
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(
                "Failed to resolve universe %s for live symbols: %r", universe_name, exc
            )
            return
        for sym in resolved:
            if sym:
                symbols.add(str(sym).upper())

    _add_from_universe("index_core")

    for universe in cfg.universes.values():
        if universe.source.type == UniverseSourceType.TASTY_WATCHLIST:
            _add_from_universe(universe.name)

    if not symbols:
        return ["SPX", "XSP"]

    return sorted(symbols)


def _build_live_quotes() -> Optional[LiveMarketDataService]:
    global _live_quotes_instance
    session = make_tasty_streaming_session_from_env()
    if session is None:
        return None

    symbols = _resolve_live_symbols()

    service = LiveMarketDataService(session=session, symbols=symbols)
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
