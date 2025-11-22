# StratDeck Live Data & Paper Trading Roadmap

This roadmap assumes your current state:

- DXLink streaming via `LiveMarketDataService` is working and integrated into `TastyProvider`.
- Live Tasty option chains are wired into pricing (`chain_pricing_adapter`).
- CLI `trade-ideas` runs in `STRATDECK_DATA_MODE=live` without DXLink/429 errors.

The goal is a robust, streaming-first, **live but paper** StratDeck v1.

---

## Phase 1 – Harden the Live Market Data Layer

### 1. Streaming health & reconnection

**Features**

- Add a simple **health API** on `LiveMarketDataService`:
  - `last_event_at: datetime | None`
  - `is_healthy(max_stale_seconds: int = 5) -> bool`
- Add **automatic reconnection**:
  - If `_stream_forever` sees a network / DXLink error, backoff and reconnect.
  - Keep `stop_event` respected so shutdown is still clean.
- Add **stale snapshot guard**:
  - Store timestamp per-symbol in the snapshot.
  - Let `get_snapshot(symbol, max_age_seconds: int | None = None)` return `None` if too old.
  - `TastyProvider._quote_from_snapshot` should be able to request “must be < X seconds old”.

**Tests**

- New tests in `tests/test_live_quotes.py`:
  - Fake streamer that stops sending events → `is_healthy` returns `False`.
  - Fake reconnect path: first `get_event` raises, second works; assert reconnection happens once, not in a tight loop.
  - Snapshot staleness:
    - Insert an old snapshot, assert `get_snapshot(..., max_age_seconds=1)` returns `None`.

---

### 2. REST fallback and rate limiting

**Features**

- Add **rate-limit awareness** inside `TastyProvider._get_json` or `_get_quote_rest`:
  - If HTTP 429 is returned:
    - Log once per symbol per window.
    - Mark that symbol as “REST cooldown” with a timestamp.
- Update `get_quote`:
  - If streaming snapshot is missing/stale and the symbol is in REST cooldown, **skip** REST instead of hammering it and fall back to TA/synthetic.

**Tests**

- In `tests/test_tasty_provider_live_quotes.py` (or new file):
  - Mock `_get_json` to return a 429 for SPX, then call `get_quote` twice:
    - First call: logs error, sets cooldown.
    - Second call: does **not** attempt REST again; uses fallback path.
  - Assert that cooldown expires after some configured period.

---

### 3. Config + env sanity for Tasty

**Features**

- Centralise Tasty config:
  - One small module or dataclass (`TastyConfig`) that reads:
    - REST: `TASTY_USER`, `TASTY_PASS`
    - Streaming: `TASTY_CLIENT_SECRET`, `TASTY_REFRESH_TOKEN`, `TASTY_IS_TEST`
  - Provide a `validate()` method that:
    - Logs clear messages if anything critical is missing.
- Add a **CLI command**:
  - `python -m stratdeck.cli tasty-health`:
    - Verifies REST & DXLink connectivity (lightweight ping, no chains).
    - Prints a short status JSON.

**Tests**

- Unit tests:
  - Missing env var → `validate()` raises or returns an error object.
  - `tasty-health` with monkeypatched `TastyProvider` & `LiveMarketDataService` returns a sane, parseable JSON.

---

## Phase 2 – Planner + Strategy Engine with Real Data

### 4. Underlying price selection is explicitly streaming-first

**Features**

- Consolidate underlying price logic into one helper, e.g. `get_underlying_price(symbol, provider)`:
  - Order of precedence:
    1. DXLink snapshot mid (fresh).
    2. REST `/market-data` mid/mark.
    3. TA/Chartist hint.
- Make `trade_planner` / `trade-ideas` **only** call this helper, not duplicate logic.

**Tests**

- For the helper:
  - Case 1: streaming snapshot exists → uses snapshot mid.
  - Case 2: no snapshot, REST returns OK → uses REST mark/mid.
  - Case 3: REST throws, TA hint available → uses TA.
  - Case 4: REST returns 429, streaming stale → falls back to TA; also verifies cooldown is set.

---

### 5. Filters and thresholds that actually bite in live mode

**Features**

- Strategy filters:
  - `min_credit_per_width`
  - `min_pop`
  - `min_ivr`
- Make sure they are:
  - Read from `strategies.yaml`.
  - Applied using the **live chain & quote** values.
- Add env toggle for **strict filters**:
  - `STRATDECK_STRICT_FILTERS=1` → hard fail / no ideas if thresholds not met.
  - Default: filters are advisory but logged.

**Tests**

- Given a fake chain + quote:
  - If `credit_per_width < min_credit_per_width`, candidate is filtered out.
  - Same for POP and IVR.
- Add one test per filter combo so you don’t accidentally regress semantics when tweaking.

---

### 6. Expand supported strategies with live data

**Features**

- Take the live stack you have for `short_put_spread_index_45d` and:
  - Add live chain pricing + DXLink support for:
    - `short_call_spread_index_45d`
    - `iron_condor_index_30d`
  - Ensure strategies share the same:
    - DTE rule,
    - Width rule,
    - Filter set.

**Tests**

- For each strategy:
  - A fixture chain with 2–4 strikes per side.
  - Assert:
    - Legs constructed correctly (short/long, strikes, expiry).
    - `spread_width` and `credit_per_width` are sane & positive.
    - POP is between 0 and 1.

---

## Phase 3 – Execution & Paper-Trade Engine

### 7. Better mid-fill model

**Features**

- Introduce a `fill_price_from_quotes(side, bid, ask)` utility:
  - Default: mid.
  - Optionally:
    - For shorts: a bit closer to bid (e.g. bid + 30% of spread).
    - For longs: closer to ask.
- Integrate into whatever code path currently computes “mid-price” for simulated fills.

**Tests**

- Unit tests:
  - `fill_price_from_quotes("short", 1.0, 1.5)` returns something between 1.0 and 1.25.
  - Edge cases: `bid == ask`, missing bid/ask, 0-width spread.

---

### 8. Simulated orders + position tracking

**Features**

- Add a simple **order log** model and persistence:
  - File-based CSV/SQLite is fine for v1 (`stratdeck.db` already exists).
  - Columns: time, symbol, strategy, side, qty, entry_price, underlying_at_entry, mode (paper/live).
- When `STRATDECK_TRADING_MODE=paper` and you run `enter-auto`:
  - Compute fill price via your new mid model.
  - Save the order to the log instead of (or in addition to) hitting Tasty.

**Tests**

- Integration-ish:
  - Run a fake `enter-auto` with a mocked `TastyProvider` and ensure exactly one row gets written with expected fields.
  - Verify that repeated runs append and don’t overwrite.

---

### 9. Live P&L and lifecycle checks (still paper)

**Features**

- A simple **positions view**:
  - Read open paper positions and mark-to-market using current quotes via `TastyProvider`.
  - Compute:
    - Current P&L
    - % to max profit
    - Days to expiry
- Basic **exit rules**:
  - 50% max profit reached.
  - X DTE remaining.
  - Hard max loss.
- Expose via CLI:
  - `python -m stratdeck.cli positions`
  - `python -m stratdeck.cli check-exits` (only logs candidates for now, no automation).

**Tests**

- Fake position with:
  - Known entry credit + chain mid at exit time.
  - Assert P&L calculation is correct.
  - Assert exit rules flag the position at the correct thresholds.

---

## Phase 4 – Orchestrator v1 (When You’re Ready)

**Features**

- A small orchestrator that can:
  - Run `trade-ideas` on a schedule.
  - Apply filters + risk rules.
  - Call `enter-auto` in **paper** mode only.
  - Periodically run `check-exits` to suggest closures.

**Tests**

- Use **fake clock + fake provider**:
  - Drive the orchestrator through a simulated day:
    - Morning scan → one paper trade.
    - Intraday move → one exit suggestion at +50%.
  - No real network calls, no real orders.

---

## Phase 5 – Observability & Guard Rails

**Features**

- Structured logging:
  - Ensure all key actions (DXLink connect/disconnect, REST 429s, paper entries, exit checks) log as single-line JSON or at least consistent tagged logs.
- “Tripwire” protections:
  - If `STRATDECK_TRADING_MODE=live`, require:
    - A specific env var/flag, e.g. `STRATDECK_LIVE_ARMED=YES`.
    - A dry-run mode by default unless explicitly confirmed.
  - In code, treat **paper** as the default.

**Tests**

- Unit tests for:
  - Live mode without `STRATDECK_LIVE_ARMED` → raises or refuses to place real orders.
  - Paper mode is always allowed.
