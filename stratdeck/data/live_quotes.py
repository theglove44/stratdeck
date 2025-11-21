from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, Optional, Sequence, Set


log = logging.getLogger(__name__)


def make_tasty_streaming_session_from_env() -> Optional[Any]:
    """
    Build a tastytrade Session for DXLink streaming using OAuth credentials.

    Required env vars:
      - TASTY_CLIENT_SECRET
      - TASTY_REFRESH_TOKEN
      - (optional) TASTY_IS_TEST = "1" for sandbox, "0"/unset for live
    """
    try:
        from tastytrade import Session as TastySession  # type: ignore
    except Exception as exc:
        log.warning("DXLink unavailable (tastytrade missing?): %r", exc)
        return None

    client_secret = os.getenv("TASTY_CLIENT_SECRET")
    refresh_token = os.getenv("TASTY_REFRESH_TOKEN")
    if not client_secret or not refresh_token:
        missing = [
            name
            for name, value in [
                ("TASTY_CLIENT_SECRET", client_secret),
                ("TASTY_REFRESH_TOKEN", refresh_token),
            ]
            if not value
        ]
        log.warning("DXLink streaming disabled: missing %s", ", ".join(missing))
        return None

    is_test = os.getenv("TASTY_IS_TEST", "0") == "1"

    try:
        return TastySession(client_secret, refresh_token, is_test=is_test)
    except Exception as exc:
        log.warning("DXLink session creation failed: %r", exc)
        return None


@dataclass
class QuoteSnapshot:
    symbol: str
    bid: Optional[Decimal]
    ask: Optional[Decimal]
    mid: Optional[Decimal]
    asof: datetime

    def is_fresh(self, max_age: timedelta) -> bool:
        now = datetime.now(timezone.utc)
        return (now - self.asof) <= max_age


class LiveMarketDataService:
    """
    Thin wrapper around tastytrade DXLink that maintains a cache of the latest quotes.

    This intentionally keeps the async streaming loop hidden behind a synchronous API.
    Tests are expected to call `_handle_quote_event` directly instead of opening real
    network connections.
    """

    def __init__(
        self,
        session: Any,
        symbols: Sequence[str],
        freshness_ttl: timedelta = timedelta(seconds=3),
        reconnect_delay: float = 5.0,
    ) -> None:
        self.session = session
        self.freshness_ttl = freshness_ttl
        self.reconnect_delay = reconnect_delay
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.RLock()
        self._quotes: Dict[str, QuoteSnapshot] = {}
        self._symbols: Set[str] = {s.upper() for s in symbols}
        self._has_seen_quote = False

    # ------------------ lifecycle ------------------

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop, name="LiveMarketDataService", daemon=True
            )
            self._thread.start()
            log.info(
                "LiveMarketDataService started symbols=%s ttl=%.2fs",
                sorted(self._symbols),
                self.freshness_ttl.total_seconds(),
            )

    def stop(self) -> None:
        # 1) tell the async side to wind down
        self._stop_event.set()

        # 2) nudge the loop so the task can exit naturally
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)

        # 3) wait for thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        log.info("LiveMarketDataService stopped")

    def __enter__(self) -> "LiveMarketDataService":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ------------------ public helpers ------------------

    def ensure_symbols(self, symbols: Iterable[str]) -> None:
        with self._lock:
            for sym in symbols:
                self._symbols.add(sym.upper())

    def get_snapshot(self, symbol: str) -> Optional[QuoteSnapshot]:
        sym = symbol.upper()
        with self._lock:
            snap = self._quotes.get(sym)
        if snap is None:
            return None
        if not snap.is_fresh(self.freshness_ttl):
            return None
        return snap

    def get_mid_price(self, symbol: str) -> Optional[Decimal]:
        snap = self.get_snapshot(symbol)
        return snap.mid if snap else None

    def is_healthy(self) -> bool:
        thread_alive = self._thread is not None and self._thread.is_alive()
        return thread_alive and self._has_seen_quote

    # ------------------ streaming internals ------------------

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._stream_forever())
        except RuntimeError as exc:
            # swallow "Event loop stopped before Future completed" if it happens
            log.debug("LiveMarketDataService loop terminated: %r", exc)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except RuntimeError:
                # loop may already be closed
                pass
            loop.close()

    async def _stream_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._stream_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # pragma: no cover - defensive logging
                log.warning("LiveMarketDataService stream error: %r", exc)
                # backoff, but still obey stop_event
                for _ in range(int(self.reconnect_delay * 10)):
                    if self._stop_event.is_set():
                        return
                    await asyncio.sleep(0.1)
            if self._stop_event.is_set():
                break
            await asyncio.sleep(self.reconnect_delay)

    async def _stream_once(self) -> None:
        if not self._symbols:
            log.warning("LiveMarketDataService no symbols configured; sleeping")
            await asyncio.sleep(1.0)
            return
        if self.session is None:
            log.warning("LiveMarketDataService has no session; stopping stream loop")
            self._stop_event.set()
            return

        try:
            from tastytrade import DXLinkStreamer  # type: ignore
            from tastytrade.dxfeed import Quote  # type: ignore
        except Exception as exc:
            log.error("LiveMarketDataService missing tastytrade dependency: %r", exc)
            self._stop_event.set()
            return

        async with DXLinkStreamer(self.session) as streamer:
            await streamer.subscribe(Quote, sorted(self._symbols))
            log.info("LiveMarketDataService subscribed symbols=%s", sorted(self._symbols))
            while not self._stop_event.is_set():
                try:
                    quote = await asyncio.wait_for(
                        streamer.get_event(Quote), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                self._handle_quote_event(quote)

    def _handle_quote_event(self, quote: Any) -> None:
        symbol = getattr(quote, "event_symbol", None) or getattr(
            quote, "eventSymbol", None
        )
        if not symbol:
            return
        bid_raw = getattr(quote, "bid_price", None)
        ask_raw = getattr(quote, "ask_price", None)
        bid = self._to_decimal(bid_raw)
        ask = self._to_decimal(ask_raw)
        mid: Optional[Decimal] = None
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            mid = (bid + ask) / Decimal(2)
        snap = QuoteSnapshot(
            symbol=str(symbol).upper(),
            bid=bid,
            ask=ask,
            mid=mid,
            asof=datetime.now(timezone.utc),
        )
        with self._lock:
            self._quotes[snap.symbol] = snap
            self._has_seen_quote = True

    @staticmethod
    def _to_decimal(val: Any) -> Optional[Decimal]:
        if val is None:
            return None
        try:
            return Decimal(str(val))
        except Exception:
            return None
