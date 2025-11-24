# Codex-Max Task: Live DXLink Quotes, 429 Fixes, and Full GitHub MCP Repo Flow

## Overview

StratDeck’s current **live** data path still relies heavily on Tasty REST endpoints for quotes, with DXLink streaming treated as an optional cache. When `LiveMarketDataService` does not have a snapshot for a symbol (or is not configured), `TastyProvider.get_quote()` falls back to `/market-data/...` HTTP calls for each request. Under realistic usage (frequent scans on SPX/XSP), this results in **HTTP 429 Too Many Requests** errors from Tasty.

This task will:

- Align StratDeck’s live data plumbing with Tasty’s *Streaming Market Data* configuration.
- Make DXLink streaming the **primary** live-quote source in live mode.
- Aggressively reduce and throttle REST quote usage.
- Add/extend tests to lock in the new behaviour.
- Run the full test suite and smoke checks.
- Use the **GitHub Docker MCP server** for full end‑to‑end repo management:
  - Create a feature branch in `theglove44/stratdeck-copilot`.
  - Apply and iterate on changes.
  - Run tests until green.
  - Commit and push to GitHub.
  - Open a pull request when done.

Codex‑Max must iterate until the final state matches this spec and **all tests are passing**.

---

## Repository & Tooling

### Repository

- **GitHub repo:** `theglove44/stratdeck-copilot`
- **Default branch:** `main`
- **Feature branch name (recommended):**  
  `feature/live-quotes-dxlink-429-fix`

### MCP Tooling

Codex‑Max **must** use the GitHub Docker MCP server and associated tools, not ad‑hoc local commands, for repo management:

- Use the **GitHub MCP** to:
  - Ensure the repo is available locally (clone if needed).
  - Create and switch to the feature branch.
  - Stage, commit, and push changes to GitHub.
  - Open a pull request against `main` on `theglove44/stratdeck-copilot`.
- Use the **filesystem/terminal MCP** tools for:
  - Editing files.
  - Running `pytest` and any CLI smoke checks.
  - Inspecting repo state (`git status`, etc.) where helpful.

Codex‑Max must treat GitHub MCP as the **source of truth** for branch and PR operations.

---

## High-Level Goals

1. **Live quotes in live mode must come from DXLink streaming**, not high‑frequency REST calls.
2. **REST quote usage must be minimal and throttled**, used only as a true fallback (or in non‑live modes).
3. **The implementation must match Tasty’s streaming model:**
   - Single authenticated session.
   - Single quote‑streamer token.
   - Single long‑lived DXLink websocket with symbol subscriptions.
4. **All existing tests must pass**, and new tests must assert:
   - In live mode, when `LiveMarketDataService` has a snapshot, `TastyProvider.get_quote()` uses it and does *not* hit REST.
   - Any REST fallback (if kept) is throttled/cached.
5. **No tests or default code paths should call real Tasty endpoints.**
   - Use mocks/fakes in tests.
6. **GitHub flow is fully managed via MCP:**
   - Feature branch created.
   - Commits pushed.
   - PR opened once tests are green and behaviour matches this spec.

---

## Files in Scope (Initial)

Investigate and potentially modify:

- `stratdeck/data/tasty_provider.py`
- `stratdeck/data/live_quotes.py`
- `stratdeck/data/factory.py`

You will likely need to **add or update tests** in:

- `tests/test_underlying_price_hint.py`
- `tests/test_live_data_adapter.py`
- `tests/test_tasty_provider_live_quotes.py` (create this if it doesn’t exist)
- Any other tests related to:
  - `TastyProvider`
  - Live data / underlying price hint
  - Live quote behaviour

Do **not** assume this list is exhaustive. If other modules participate in live quote retrieval, include them as needed.

---

## Current Behaviour (Summary)

### TastyProvider

File: `stratdeck/data/tasty_provider.py`

Observed behaviour:

- Manages login/session using `requests.Session` against the Tasty REST API.
- Caches **IVR** for each symbol for 300 seconds (`/market-metrics/IVR`).
- Uses `LiveMarketDataService` only if provided and if it has a snapshot; otherwise it falls back to REST for quotes:

  ```python
  def get_quote(self, symbol: str) -> Dict[str, Any]:
      sym = symbol.upper()
      live_quote = self._quote_from_snapshot(sym)
      if live_quote is not None:
          return live_quote
      return self._get_quote_rest(sym)
  ```

- `_get_quote_rest` hits `/market-data/Index/{symbol}` or `/market-data/Equity/{symbol}`, without caching or throttling.
- Under frequent scans (SPX/XSP), this leads to many REST calls and **429** errors.

### LiveMarketDataService (expected role)

File: `stratdeck/data/live_quotes.py` (implementation to be inspected).

Expected responsibilities:

- Encapsulate Tasty’s DXLink streaming:
  - Authenticate with Tasty.
  - Obtain a quote‑streamer token.
  - Open a DXLink websocket.
  - Subscribe to quotes (and greeks if needed) for specified symbols.
- Maintain a mapping of latest snapshots per symbol.
- Expose methods like:
  - `get_snapshot(symbol: str) -> Optional[QuoteSnapshot]`
  - Optional: `wait_for_snapshot(symbol: str, timeout: float) -> Optional[QuoteSnapshot]`

Currently, `TastyProvider` treats this as optional and non‑authoritative for live quotes.

---

## Desired Behaviour

### 1. DXLink as primary live quote source in live mode

For `STRATDECK_DATA_MODE=live`:

- `TastyProvider` must be constructed with a **non‑None** `LiveMarketDataService` instance that:
  - Uses a single Tasty `Session`.
  - Uses a single quote‑streamer token.
  - Maintains a single, long‑lived DXLink websocket with subscriptions.

- `TastyProvider.get_quote(symbol)` must:

  1. **Check DXLink snapshots first** via `LiveMarketDataService`:
     - If a snapshot exists, compute bid/ask/mid and return it.
  2. Optionally **wait briefly** for an initial snapshot if none is present yet:
     - e.g. `wait_for_snapshot(symbol, timeout=0.5)`.
  3. Only if no snapshot is available after that brief wait should it consider any fallback.
     - In live mode, that fallback should *not* spam REST on every call.

### 2. REST quotes minimised and throttled

For REST quotes:

- **Live mode:**
  - Use DXLink snapshots exclusively whenever possible.
  - If a REST fallback is retained:
    - It must use a **per‑symbol cache with a TTL** (e.g. at most one REST call every N seconds per symbol).
    - REST usage must be clearly marked in the returned data (e.g. `source="rest-fallback"`).
    - 429 responses must not trigger immediate tight retry loops.

- **Mock/offline modes:**
  - Existing behaviour relying on mocks/fakes may remain unchanged.

### 3. LiveMarketDataService implements the Tasty streaming model

In `stratdeck/data/live_quotes.py`:

- Ensure:

  - A single Tasty `Session` is used.
  - A single quote‑streamer token is requested and reused.
  - A single, long‑lived DXLink websocket is maintained.

- The service must:

  - Subscribe/unsubscribe to symbols as needed.
  - Maintain a threadsafe/async‑safe mapping: `symbol -> latest snapshot`.
  - Provide:
    - `get_snapshot(symbol)` (non‑blocking).
    - `wait_for_snapshot(symbol, timeout)` (optional but preferred).

- Avoid:

  - Creating a new DXLink streamer or connection per scan or per quote.
  - Requesting new quote tokens in a tight loop.

### 4. Factory wiring guarantees live streaming in live mode

In `stratdeck/data/factory.py`:

- For `STRATDECK_DATA_MODE=live`:

  - Construct a `LiveMarketDataService` instance once (backed by session + DXLink).
  - Pass it into `TastyProvider(live_quotes=live_quotes_service)`.
  - Ensure all live‑mode code paths that call `get_provider()` receive this streaming‑backed `TastyProvider`.

- For other modes, existing providers remain as-is.

---

## Detailed Implementation Requirements

### A. Refactor `TastyProvider.get_quote` and related logic

In `stratdeck/data/tasty_provider.py`:

1. Change `get_quote` to be DXLink‑first in live mode:

   - If `self._live_quotes` is set:
     - Try `get_snapshot(symbol)`.
     - Optionally try `wait_for_snapshot(symbol, timeout=...)`.
     - If still no snapshot, decide on a **limited** fallback policy (e.g. return an “empty” live quote or use throttled REST).
   - If `self._live_quotes` is `None`:
     - Use REST, but consider adding caching to avoid over‑hitting the endpoint.

2. Implement a per‑symbol REST quote cache (if REST fallback is retained):

   ```python
   self._quote_cache: Dict[str, Dict[str, Any]] = {}
   self._quote_cache_ts: Dict[str, float] = {}
   self._quote_cache_ttl = 30.0  # example TTL in seconds
   ```

   With helper:

   ```python
   def _get_quote_rest_throttled(self, symbol: str) -> Dict[str, Any]:
       # If we have a recent cached quote (within TTL), return it.
       # Otherwise call _get_quote_rest, cache the result, and return it.
   ```

3. Ensure clear logging and source tagging:

   - DXLink path: `source="dxlink"`.
   - REST fallback: `source="rest-fallback"`.

4. Do **not** implement any automatic tight retry loops after 429. The reduction in REST use via DXLink + throttling should eliminate most 429s.

### B. Ensure `LiveMarketDataService` is a single-session / single-stream abstraction

In `stratdeck/data/live_quotes.py`:

- Confirm or enforce that:

  - A single `Session` and quote‑streamer token are used for the service.
  - A single DXLink streamer/websocket is used across all symbols.
  - Reconnect and resubscribe logic exists (or is added) for robustness.

- Provide a clean, testable API:

  - `get_snapshot(symbol: str) -> Optional[QuoteSnapshot]`
  - `wait_for_snapshot(symbol: str, timeout: float) -> Optional[QuoteSnapshot]` (if achievable)

- Avoid duplicate or per‑call streamer initialisation.

### C. Factory changes for live streaming provider

In `stratdeck/data/factory.py`:

- For `STRATDECK_DATA_MODE=live`:

  - Instantiate one `LiveMarketDataService`.
  - Instantiate `TastyProvider(live_quotes=live_quotes_service)`.
  - Ensure all consumers of `get_provider()` in live mode use this provider.

---

## Testing Requirements

### 1. Unit / integration tests

Add or update tests to validate:

1. **DXLink priority in live mode**

   - Use a fake `LiveMarketDataService` that returns a synthetic snapshot.
   - Inject into `TastyProvider`.
   - Assert that:
     - `get_quote(symbol)` uses the snapshot’s values.
     - `_get_quote_rest` / REST path is not called (e.g. monkeypatch it to raise if invoked).

2. **Behaviour when no snapshot is available**

   - Fake `LiveMarketDataService` returns `None`.
   - Test the chosen policy:
     - e.g., returns a “no data yet” quote, or falls back to throttled REST.
   - Assert that:
     - Result structure and `source` field matches the policy.
     - Any REST invocation is going through the throttling helper.

3. **REST throttling semantics**

   - Monkeypatch `_get_quote_rest` to count calls.
   - Call `get_quote(symbol)` multiple times under conditions that force fallback.
   - Assert that:
     - `_get_quote_rest` is called at most once per TTL interval per symbol.

4. **Existing tests**

   - Adjust expectations where they rely on old behaviour.
   - Pay attention to:
     - `tests/test_underlying_price_hint.py`
     - `tests/test_live_data_adapter.py`
     - Any tests that currently assume REST is always used.

### 2. No live network calls in tests

- All tests must run without network access to Tasty.
- Use mocks/fakes/monkeypatching to simulate:
  - `LiveMarketDataService`.
  - REST responses where strictly necessary.

---

## Post-Fix Commands & Checks

From the repo root.

### 1. Install dependencies (if needed)

```bash
pip install -e ".[dev]"
```

### 2. Run the full test suite

```bash
pytest -q
```

All tests must pass.

### 3. Optional CLI smoke check (non-live mode)

Only if the default does **not** hit real Tasty endpoints, run something like:

```bash
export STRATDECK_DATA_MODE=mock

python -m stratdeck.cli trade-ideas   --strategy short_put_spread_index_45d   --universe index_core   --json-output
```

Ensure the command completes and the JSON is structurally valid.

Do **not** introduce any CI‑critical checks that require real Tasty credentials or a live DXLink connection.

---

## GitHub MCP Repo Management Flow

Codex‑Max must use the GitHub Docker MCP server for **all** git/GitHub operations in this task.

### 1. Sync & Branch Creation

Using GitHub MCP + terminal MCP:

1. Ensure `theglove44/stratdeck-copilot` is available locally (clone via MCP if needed).
2. Check out `main` and sync:

   ```bash
   git checkout main
   git pull
   ```

3. Create and switch to the feature branch via MCP‑driven git commands:

   ```bash
   git checkout -b feature/live-quotes-dxlink-429-fix
   ```

### 2. Implementation Loop

Repeat this loop until done:

1. **Analyse:**
   - Inspect:
     - `stratdeck/data/tasty_provider.py`
     - `stratdeck/data/live_quotes.py`
     - `stratdeck/data/factory.py`
     - Relevant tests.
   - Understand current live quote call paths and where 429s would be triggered.

2. **Implement:**
   - Edit files via filesystem/terminal MCP tools.
   - Follow the “Detailed Implementation Requirements” section.

3. **Test:**
   - Run:

     ```bash
     pytest -q
     ```

   - If any tests fail:
     - Inspect failure output.
     - Adjust implementation and/or tests.
     - Re-run `pytest -q`.
   - Repeat until **all tests pass**.

4. **Local sanity checks:**
   - Optionally run non‑live CLI smoke checks in mock mode to confirm normal workflows still behave correctly.

5. **Git status and diff:**
   - Use terminal MCP commands:

     ```bash
     git status
     git diff
     ```

   - Verify only the intended files are changed.

### 3. Commit & Push via GitHub MCP

Once tests are green and behaviour matches the spec:

1. Stage changes:

   ```bash
   git add stratdeck/data/tasty_provider.py stratdeck/data/live_quotes.py stratdeck/data/factory.py tests/
   ```

   (Adjust paths if more files are changed.)

2. Commit:

   ```bash
   git commit -m "Use DXLink streaming for live quotes and throttle REST fallbacks"
   ```

3. Push the branch to GitHub:

   ```bash
   git push -u origin feature/live-quotes-dxlink-429-fix
   ```

All of the above should be executed via the GitHub Docker MCP / terminal MCP environment, not by assuming a local developer shell outside MCP.

### 4. Open a Pull Request

Using the GitHub MCP:

- Open a PR from `feature/live-quotes-dxlink-429-fix` → `main` on `theglove44/stratdeck-copilot`.

PR title suggestion:

> Use DXLink streaming for live quotes and reduce REST 429s

PR description should summarise:

- The shift to DXLink‑first live quotes in live mode.
- Introduction of throttled REST fallback (if present).
- The tests added/updated to assert the new behaviour.
- Confirmation that `pytest -q` passes.

---

## Done Criteria

Codex‑Max is finished when **all** of the following are true:

1. **Implementation**
   - In live mode (`STRATDECK_DATA_MODE=live`), `TastyProvider.get_quote()`:
     - Uses DXLink snapshots when available.
     - Only uses REST in a throttled, clearly marked fallback path (if at all).
   - `LiveMarketDataService` provides a single‑session, single‑stream abstraction over DXLink.
   - Factory wiring guarantees a streaming‑backed provider in live mode.

2. **Testing**
   - `pytest -q` passes with no failures.
   - New/updated tests verify:
     - DXLink‑first behaviour.
     - REST throttling semantics.
     - No live network dependency in tests.

3. **GitHub / MCP Flow**
   - All changes are committed to branch `feature/live-quotes-dxlink-429-fix`.
   - The branch is pushed to `theglove44/stratdeck-copilot` on GitHub.
   - A pull request from `feature/live-quotes-dxlink-429-fix` into `main` is opened via GitHub MCP.

Only when these conditions are met should Codex‑Max consider this task complete.
