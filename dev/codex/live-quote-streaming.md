# PROJECT: StratDeck – Live Quote Streaming (DXLink) Slice

Task file: `dev/codex/live-quote-streaming.md`  
Branch: `feature/live-quote-streaming`  
Runner: `./scripts/codex-task.sh live-quote-streaming`

---

## 1. Context

StratDeck currently has partial “live” support:

- `stratdeck/data/tasty_provider.py` exposes a `TastyProvider` that:
  - Logs into Tastytrade.
  - Provides `get_quote(symbol: str)` via **REST** `/quotes`.
  - Provides `get_option_chain(symbol, expiry)` via **REST** `/option-chains/{symbol}/nested`.

- `stratdeck/tools/chain_pricing_adapter.py`:
  - In `STRATDECK_DATA_MODE=live`, uses **real Tasty option chains** for pricing.
  - Falls back to synthetic/yfinance-style chains otherwise.
  - Calculates `credit`, `credit_per_width`, `pop`, `width`, and per-leg pricing.

- `stratdeck/agents/trade_planner.py`:
  - Uses TA / Chartist to derive an initial `underlying_price_hint`.
  - Has helpers that call `TastyProvider.get_quote` in live mode for a snapshot price.

There are tests for:

- Live option-chain pricing:
  - `tests/test_tasty_chains_live.py`
- Underlying price behaviour and fallbacks:
  - `tests/test_trade_planner_underlying_price.py`
  - `tests/test_underlying_price_hint.py`

**Problem:** We currently treat REST `/quotes` as if it were a live data stream:

- Live mode repeatedly calls `get_quote` (REST), especially for SPX/XSP.
- This causes `HTTP 429 Too Many Requests` from Tastytrade.
- Architecturally, REST should be “control plane” (slow, infrequent) and **DXLink** should be the “data plane” for live quotes.

This slice introduces a small, focused **live quote streaming layer** using the official Tastytrade DXLink client, backed by an in-process quote cache, and wires it cleanly into `TastyProvider` and the planner.

---

## 2. Goals and Non-Goals

### 2.1 Goals

1. Implement a `LiveMarketDataService` that:
   - Uses `tastytrade.DXLinkStreamer` and `Quote` events.
   - Subscribes to a small symbol universe (initially SPX, XSP).
   - Maintains a thread-safe in-memory cache of the latest quotes.
   - Exposes a simple synchronous API to get the latest snapshot / mid price.

2. Integrate `LiveMarketDataService` with `TastyProvider`:
   - `TastyProvider` optionally accepts a `LiveMarketDataService` instance.
   - `TastyProvider.get_quote(symbol)`:
     - **First**: tries to read a fresh quote from the streaming cache.
     - **Fallback**: existing REST `/quotes` implementation.

3. Ensure the trade planner and pricing code use the streaming-backed `get_quote`:
   - In `STRATDECK_DATA_MODE=live`, “underlying price” comes from DXLink (if available).
   - Existing TA / synthetic fallbacks remain available if streaming is unavailable.

4. All tests must remain fully **offline-safe**:
   - No real network calls or live DXLink connections during tests.
   - Streaming behaviour is covered with mocks / fake data / direct invocation of handlers.

### 2.2 Non-Goals

- No orchestrator / daemon changes.
- No streaming of **option quotes** or greeks in this slice.
- No change to `STRATDECK_DATA_MODE` / `STRATDECK_TRADING_MODE` semantics.
- No new configuration system for streaming symbols; for now we can hardcode a minimal set (SPX, XSP) when wiring.

---

## 3. Constraints and Conventions

- **Repo:** `~/Projects/stratdeck-copilot`
- **Python:** 3.9
- **Tests:** `python -m pytest`
- **CLI entry:** `python -m stratdeck.cli ...`
- **Branch for this work:** `feature/live-quote-streaming`

Follow existing project conventions:

- Read `AGENTS.md` before writing any code:
  - Logging (no `print`, use project logger).
  - Error handling (no bare `except`, structured logging).
  - Type hints, docstring style.
  - Test isolation and environment behaviour.

Testing:

- All new tests must run without Tastytrade network access.
- Mock / fake DXLink or call internal handlers directly.
- Do **not** depend on external environment variables for tests.

---

## 4. Design

### 4.1 New Module: `stratdeck/data/live_quotes.py`

Create a new module to encapsulate the streaming logic and in-process cache.

#### 4.1.1 `QuoteSnapshot` dataclass

Implement a small snapshot object that represents a single symbol’s current quote:

- Location: `stratdeck/data/live_quotes.py`

Structure:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional


@dataclass
class QuoteSnapshot:
    symbol: str                # Normalized symbol, e.g. "SPX"
    bid: Optional[Decimal]
    ask: Optional[Decimal]
    mid: Optional[Decimal]
    asof: datetime             # UTC timestamp of last update

    def is_fresh(self, max_age: timedelta) -> bool:
        # Return True if this snapshot is not older than `max_age`.
        # Use datetime.now(timezone.utc) for comparison.
        ...
```

Notes:

- `mid` should be precomputed when we update the snapshot.
- `is_fresh` is used to guard against stale quotes.

#### 4.1.2 `LiveMarketDataService` class

Provide a synchronous façade over an async DXLink event loop, with a thread-safe cache.

**Public API:**

```python
class LiveMarketDataService:
    def __init__(
        self,
        session: "Session",
        symbols: Sequence[str],
        freshness_ttl: timedelta = timedelta(seconds=3),
        reconnect_delay: float = 5.0,
    ) -> None: ...

    def start(self) -> None:
        # Start the background streaming thread (idempotent).
        ...

    def stop(self) -> None:
        # Stop the background thread and close any DXLink resources.
        ...

    def __enter__(self) -> "LiveMarketDataService": ...
    def __exit__(self, exc_type, exc, tb) -> None: ...

    def ensure_symbols(self, symbols: Iterable[str]) -> None:
        # Add symbols to the tracked set. For v1 it's enough to update the set;
        # any resubscribe behaviour can be handled on reconnect.
        ...

    def get_snapshot(self, symbol: str) -> Optional[QuoteSnapshot]:
        # Return a fresh QuoteSnapshot for `symbol` if one exists and is not older
        # than freshness_ttl; otherwise return None.
        ...

    def get_mid_price(self, symbol: str) -> Optional[Decimal]:
        # Convenience wrapper around get_snapshot(symbol)?.mid.
        ...

    def is_healthy(self) -> bool:
        # Return True if the service appears to be running and has seen at least
        # one quote event for any symbol.
        ...
```

**Internal behaviour:**

- Use `tastytrade.DXLinkStreamer` and `tastytrade.dxfeed.Quote` events.
- Run an `asyncio` loop in a dedicated background thread:
  - Thread name: e.g. `"LiveMarketDataService"`.
  - Uses a private `_run_loop()` method to:
    - Create a new event loop (`asyncio.new_event_loop()`).
    - Run `_stream_forever()` until stopped.
- `_stream_forever`:
  - Outer reconnect loop:
    - Calls `_stream_once()`.
    - On any exception, log the exception and sleep `reconnect_delay` seconds before retrying (unless stopping).
- `_stream_once`:
  - If there are no symbols configured:
    - Log a warning, sleep briefly, return.
  - Create `DXLinkStreamer(session)` as an async context manager.
  - `await streamer.subscribe(Quote, symbols)` once.
  - In a `while not stop_event` loop:
    - `quote = await streamer.get_event(Quote)`
    - Call a handler `_handle_quote_event(quote)`.

**Cache / locking details:**

- Use a `threading.RLock` to guard access to an internal dict:
  - `self._quotes: Dict[str, QuoteSnapshot]`
- `_handle_quote_event(quote)`:
  - Normalise symbol (e.g. `quote.event_symbol`).
  - Extract best bid/ask from the Quote (bid_price / ask_price).
  - Compute `mid` only if both bid and ask are non-null and > 0.
  - Create `QuoteSnapshot(symbol, bid, ask, mid, asof=datetime.now(timezone.utc))`.
  - Store it into `self._quotes[symbol]` under the lock.

**Error handling and logging:**

- Use the project logger: `from stratdeck.core.logging import get_logger` (or whatever logger factory exists).
- Log:
  - Start/stop events.
  - Subscription details (symbols).
  - Connection failures and reconnect attempts.
  - Don’t spam logs on every quote; only log summaries / important events.

**Testing hooks:**

- It’s acceptable for tests to call `_handle_quote_event` directly even though it’s a "private" method.
- Do **not** open a real DXLink connection in any test.
- The service’s streaming loop must not run during unit tests.

---

### 4.2 Integration: `TastyProvider`

Update `stratdeck/data/tasty_provider.py` to consume `LiveMarketDataService`.

#### 4.2.1 Constructor changes

- Add an optional `live_quotes` parameter:

```python
from typing import Optional
from datetime import timedelta

from stratdeck.data.live_quotes import LiveMarketDataService, QuoteSnapshot

class TastyProvider:
    def __init__(
        self,
        session: "Session",
        # existing args...
        live_quotes: Optional[LiveMarketDataService] = None,
    ) -> None:
        self._session = session
        # existing setup...
        self._live_quotes = live_quotes
        self._live_quote_max_age = timedelta(seconds=3)  # or reuse freshness_ttl
```

- Do not change existing call sites yet; they’ll be updated as part of this task.

#### 4.2.2 `get_quote` streaming-first behaviour

- Modify `get_quote(symbol: str)` to:

1. If `self._live_quotes` is provided:
   - Call `self._live_quotes.get_snapshot(symbol)`.
   - If a non-`None` snapshot is returned:
     - Map it into the existing quote return type (e.g. `QuoteData`), preserving the public structure.
     - Use snapshot.mid as the “mid” value (and include bid/ask where appropriate).
     - Optionally set a `source` field to `"dxlink"` if such a field exists.

2. If no streaming snapshot is available (service missing or snapshot stale or symbol unknown):
   - Fall back to the existing REST-based behaviour (current `get_quote` internals).
   - Do **not** change the REST path’s logic except what’s necessary to accommodate the new call sequence.

- Make sure this change does not affect non-live modes where `TastyProvider` is constructed without `live_quotes`.

---

### 4.3 Planner Integration: Underlying Price

Verify and, if needed, adjust `stratdeck/agents/trade_planner.py` so that:

- The canonical helper for getting an underlying price (e.g. `get_underlying_price` or similar) calls `provider.get_quote(symbol)` **only**, and does not talk to REST directly.
- It then uses:
  - `quote.mid` (preferred) or
  - `quote.last` or other price field as a fallback.
- If no suitable quote is available (no streaming snapshot and REST fails), it falls back to the **existing** TA / synthetic path (no change in behaviour there).

If the code already behaves this way, no further changes are needed beyond ensuring the type/fields of the quote object still line up after the `TastyProvider.get_quote` refactor.

---

### 4.4 Wiring: Where `LiveMarketDataService` Is Created

Find the place(s) where `TastyProvider` is constructed for live mode. This is likely in:

- `stratdeck/cli.py`, or
- A helper/builder used by the CLI and agents.

Extend that wiring as follows for **live data mode only**:

1. Read `STRATDECK_DATA_MODE` (or the existing helper you already use).
2. If `data_mode == "live"`:
   - Construct a `LiveMarketDataService` as:

     ```python
     live_symbols = ["SPX", "XSP"]  # v1: small, fixed set
     live_quotes = LiveMarketDataService(session=session, symbols=live_symbols)
     live_quotes.start()
     ```

   - Pass `live_quotes` into `TastyProvider`.

3. Ensure clean lifetime management:
   - **Preferred**: Wrap the CLI command in a context manager so the service is always stopped:

     ```python
     if data_mode == "live":
         with LiveMarketDataService(session, symbols=live_symbols) as live_quotes:
             provider = TastyProvider(session=session, live_quotes=live_quotes, ...)
             run_trade_ideas(provider, ...)
     else:
         provider = TastyProvider(session=session, ...)
         run_trade_ideas(provider, ...)
     ```

   - If using a context manager is not feasible in all call sites, ensure `stop()` is called explicitly at the end of the command.

4. Keep this initial wiring conservative:
   - Do not attempt to pull dynamic symbol lists from universes in this slice.
   - Add a small comment noting that a future slice can expand `live_symbols` using universe/strategy config.

---

## 5. Tests

Add new tests and adjust existing ones where needed.

### 5.1 New: `tests/test_live_market_data_service.py`

Add tests for the cache and freshness logic without running any real async event loop or DXLink connection.

Suggested patterns:

1. **Snapshot freshness**

   - Create a `QuoteSnapshot` with `asof` set to “now minus 1 second”.
   - Call `is_fresh(max_age=timedelta(seconds=3))` → expect `True`.
   - Create another snapshot “now minus 10 seconds”.
   - Call `is_fresh(max_age=timedelta(seconds=3))` → expect `False`.

2. **Service caching via handler**

   - Construct a `LiveMarketDataService` instance with a dummy session and symbols (you don’t need a real `Session` object for these tests if you never start the loop).
   - Call its `_handle_quote_event` directly with a small fake object that has the same attributes as a `Quote` (`event_symbol`, `bid_price`, `ask_price`).
   - Call `get_snapshot(symbol)` and assert:
     - Snapshot is not `None`.
     - `bid`, `ask`, and `mid` behave as expected.
     - `is_fresh` with the default `freshness_ttl` is `True`.

3. **Stale snapshot handling**

   - After creating a snapshot, manually modify its `asof` to be older than `freshness_ttl` (e.g. now minus 10 seconds).
   - Call `get_snapshot(symbol)` and expect `None`.

Notes:

- These tests should **never** call `start()` or open any DXLink connection.
- Fake the quote event object with a simple `types.SimpleNamespace` or small class.

### 5.2 New: `tests/test_tasty_provider_live_quotes.py`

Add a test module to verify `TastyProvider.get_quote`’s streaming-first behaviour.

Patterns:

1. **Streaming snapshot preferred**

   - Create a fake `LiveMarketDataService` class inside the test:

     ```python
     class FakeLiveQuotes:
         def __init__(self, snapshot):
             self._snapshot = snapshot
         def get_snapshot(self, symbol):
             return self._snapshot
     ```

   - Create a fake `TastyProvider` instance:
     - Inject `live_quotes=FakeLiveQuotes(snapshot)`.
     - Monkeypatch / stub out the REST `_get_quote_rest` implementation to **raise** or record calls if hit.
   - Call `provider.get_quote("SPX")`.
   - Assert:
     - The returned object uses the snapshot values for mid/bid/ask.
     - The REST method was **not** called.

2. **REST fallback when streaming missing**

   - Use `FakeLiveQuotes` that always returns `None`.
   - Monkeypatch `_get_quote_rest` to return a fake REST quote object.
   - Call `provider.get_quote("SPX")`.
   - Assert:
     - The return value matches the fake REST quote.
     - `LiveMarketDataService.get_snapshot` was called.
     - The behaviour is unchanged from previous REST-only logic.

Keep these tests small and focused on selection logic. Do not assert logging text or network behaviour.

### 5.3 Existing Tests

- Run the full test suite with `python -m pytest`.
- Ensure that:
  - `tests/test_tasty_chains_live.py` still passes.
  - `tests/test_trade_planner_underlying_price.py` and `tests/test_underlying_price_hint.py` still pass.
- If any tests depend on internal `TastyProvider.get_quote` behaviour that you changed, update them minimally to align with the new, streaming-first semantics (without changing their intent).

---

## 6. Implementation Workflow (for Codex-Max)

Follow this sequence exactly:

1. **Read context and conventions**
   - Open and read:
     - `AGENTS.md`
     - `stratdeck/data/tasty_provider.py`
     - `stratdeck/agents/trade_planner.py`
     - `stratdeck/tools/chain_pricing_adapter.py` (for how underlying price is used)
     - Existing tests:
       - `tests/test_tasty_chains_live.py`
       - `tests/test_trade_planner_underlying_price.py`
       - `tests/test_underlying_price_hint.py`

2. **Implement live quote module**
   - Create `stratdeck/data/live_quotes.py`.
   - Implement:
     - `QuoteSnapshot` dataclass.
     - `LiveMarketDataService` with:
       - Background thread + event loop.
       - DXLink subscription to `Quote` events.
       - In-memory cache with TTL.
       - Thread-safe `get_snapshot`, `get_mid_price`, `is_healthy`.
     - Logging consistent with `AGENTS.md`.

3. **Wire `LiveMarketDataService` into `TastyProvider`**
   - Modify `stratdeck/data/tasty_provider.py`:
     - Add optional `live_quotes` parameter.
     - Update `__init__` to store it and configure a freshness TTL.
     - Update `get_quote(symbol)` to:
       - Check `live_quotes.get_snapshot(symbol)` first.
       - Return a quote mapped from `QuoteSnapshot` when available.
       - Fall back to the existing REST path when no fresh snapshot exists.
   - Ensure the public return type and field names for `get_quote` remain compatible with existing callers.

4. **Ensure planner uses provider properly**
   - Review `stratdeck/agents/trade_planner.py`:
     - Confirm any “underlying price” helper uses `provider.get_quote` and not direct REST.
     - Confirm TA / synthetic fallback remains as a backup if no quote is available.
   - Only make minimal changes required to route everything through the updated `get_quote`.

5. **Add tests**
   - Add `tests/test_live_market_data_service.py` with:
     - Snapshot freshness tests.
     - Cache update tests via `_handle_quote_event`.
     - Stale snapshot behaviour.
   - Add `tests/test_tasty_provider_live_quotes.py` with:
     - Streaming snapshot preferred over REST.
     - REST fallback when streaming snapshot is missing.

6. **Run tests**
   - Run `python -m pytest`.
   - If any tests fail:
     - Fix issues while keeping the design described above.
     - Re-run until green.

7. **Show changes**
   - Run:
     - `git status`
     - `git diff`
   - Include the output of these commands in the final Codex-Max reply.

---

## 7. Acceptance Criteria

This slice is complete when:

1. **Code design**
   - `stratdeck/data/live_quotes.py` exists with:
     - `QuoteSnapshot` and `LiveMarketDataService` implemented as described.
   - `TastyProvider`:
     - Accepts an optional `LiveMarketDataService`.
     - `get_quote(symbol)` prefers streaming snapshots and falls back to REST.
   - Planner code that needs an underlying price calls `provider.get_quote` and works unchanged in non-live modes.

2. **Tests**
   - New tests:
     - `tests/test_live_market_data_service.py`
     - `tests/test_tasty_provider_live_quotes.py`
   - All tests pass via `python -m pytest` with no network access.

3. **Behaviour**
   - In `STRATDECK_DATA_MODE=live` (when wired in CLI/agents):
     - For symbols SPX/XSP, `TastyProvider.get_quote` uses streaming data when available.
     - REST `/quotes` is only used as a slow fallback.
   - In non-live modes or when streaming is not constructed:
     - Behaviour remains identical to the pre-existing REST-only implementation.
