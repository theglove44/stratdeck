# PROJECT: StratDeck Copilot – DXLink Session Fix for CLI (Invalid JWT)

You are working on the **LIVE DATA / QUOTE STREAMING** layer of StratDeck, *specifically* how it is wired into the **CLI**. The goal is to make the CLI use the same **working DXLink session pattern** that is already verified in `tests/live_quote_smoke.py`, and stop emitting:

> DXLink session creation failed: ... invalid_grant / Invalid JWT

The end state: when running `stratdeck.cli trade-ideas` in live mode, DXLink streaming comes up cleanly and `TastyProvider` uses the DXLink-backed quote cache instead of hammering REST and hitting 429s.

---

## CONTEXT

Repo root: `~/Projects/stratdeck-copilot`

You MUST:

- Read and follow `AGENTS.md` (logging, style, safety, tests, etc.).
- Respect existing env var semantics:
  - REST side (`TastyProvider`) still uses `TASTY_USER` / `TASTY_PASS` for now.
  - Streaming side (DXLink) should use **OAuth-style** env vars:
    - `TASTY_CLIENT_SECRET`
    - `TASTY_REFRESH_TOKEN`
    - `TASTY_IS_TEST` (optional: `"1"` for sandbox, `"0"` / unset for live).

Current known state:

- `stratdeck/data/tasty_provider.py`:
  - Has `TastyProvider` with streaming-first `get_quote`:
    - Tries `_quote_from_snapshot(...)` (DXLink-backed cache).
    - Falls back to `_get_quote_rest(...)` (REST) if no snapshot.
  - This part is **unit-tested and working**.

- `stratdeck/data/live_quotes.py`:
  - Provides `LiveMarketDataService` which:
    - Wraps `DXLinkStreamer`.
    - Runs an asyncio loop in a background thread.
    - Maintains a snapshot cache for symbols (SPX/XSP, etc.).
  - Shutdown behaviour has been recently fixed (no more “event loop is closed” spam).

- `tests/test_tasty_provider_live_quotes.py`:
  - Tests `_quote_from_snapshot`, `get_quote` selection logic, and `_get_quote_rest`.
  - All **passing**.

- `tests/live_quote_smoke.py`:
  - Uses **OAuth-style DXLink session** to subscribe to SPX/XSP.
  - Pattern:
    - `Session(TASTY_CLIENT_SECRET, TASTY_REFRESH_TOKEN, is_test=...)`
    - Wraps `LiveMarketDataService` in a context manager.
    - Uses `TastyProvider(live_quotes=live)` and prints SPX mid + `source`.
  - Verified behaviour:
    - First tick: `source=None` (REST).
    - Subsequent ticks: `source=dxlink` (streaming).
    - No shutdown errors.

- CLI run (problem):

  ```text
  DXLink session creation failed: TastytradeError("Couldn't parse response: {'error_code': 'invalid_grant', 'error_description': 'Invalid JWT'}")
  underlying_price_hint live quote failed symbol=SPX error=RuntimeError('Tastytrade error 429: ... Too Many Requests ...')
  ```

  ⇒ The CLI uses a **different DXLink session construction path** that is feeding an **invalid JWT** to the streaming infrastructure (likely a non-OAuth token or old flow).

---

## GOAL

**Unify DXLink session creation and fix “Invalid JWT” in the CLI.**

Specifically:

1. **Find where the CLI constructs / configures the streaming side**:
   - Where `LiveMarketDataService` (or DXLink/Session) is built for CLI runs (e.g. trade-ideas).
   - Where the “DXLink session creation failed: ... Invalid JWT” log is emitted.

2. **Refactor DXLink session creation to reuse the working pattern from `tests/live_quote_smoke.py`:**
   - A shared helper that:
     - Reads `TASTY_CLIENT_SECRET`, `TASTY_REFRESH_TOKEN`, `TASTY_IS_TEST`.
     - Constructs a `tastytrade.Session` with these OAuth credentials.
     - Is used by both:
       - CLI live mode wiring.
       - The smoke test (if appropriate), to keep them in sync.

3. **Ensure that when CLI is run in live mode (`STRATDECK_DATA_MODE=live`):**
   - DXLink starts successfully with the OAuth session.
   - `TastyProvider` used by the planner is constructed with `live_quotes=LiveMarketDataService`.
   - After the stream has warmed up, SPX/XSP quotes are served from DXLink snapshot cache (i.e. `source="dxlink"`), not from REST.

4. **Keep existing behaviour intact**:
   - Non-live data modes remain unchanged (synthetic/yfinance/TA fallbacks).
   - Existing tests continue to pass.
   - REST login / account logic in `TastyProvider` (username/password) stays as-is for now.

---

## SCOPE

You **should** touch:

- `stratdeck/data/live_quotes.py`  
  (or a new small helper module if more appropriate, e.g. `stratdeck/data/tasty_session.py`)

- `stratdeck/cli.py`  
  (or whatever module actually wires:
  - Tasty provider(s),
  - Live market data service,
  - Strategy/planner pipeline for `trade-ideas`.)

- `tests/live_quote_smoke.py`  
  (only if needed to reuse shared helper; keep behaviour equivalent.)

You **may** add small, targeted tests that validate:

- The CLI live-data builder uses the correct session helper in live mode.
- Envs are wired correctly.

You **must not**:

- Change the core signature or external semantics of `stratdeck.cli` commands.
- Break or remove existing tests.
- Change REST login behaviour in `TastyProvider` (username/password) in this task.

---

## NON-GOALS

- No orchestrator changes.
- No strategy logic changes.
- No new quote/greeks computations.
- No larger refactor of authentication model across the whole project.

This task is narrowly about **DXLink session wiring for CLI live mode**.

---

## REQUIREMENTS

1. **Shared DXLink Session Helper**

   Introduce a helper function (exact name up to you, but be consistent), e.g.:

   ```python
   # e.g. in stratdeck/data/live_quotes.py or a new stratdeck/data/tasty_session.py

   from tastytrade import Session

   def make_tasty_streaming_session_from_env() -> Session:
       """
       Construct a tastytrade.Session for DXLink using OAuth env vars:

         - TASTY_CLIENT_SECRET
         - TASTY_REFRESH_TOKEN
         - TASTY_IS_TEST (optional, "1" for sandbox)
       """
       ...
   ```

   Behaviour:

   - Reads `TASTY_CLIENT_SECRET` and `TASTY_REFRESH_TOKEN` from `os.environ`.
     - If either is missing, raises a clear, logged error (respecting AGENTS.md logging conventions) and returns `None` or propagates appropriately.
   - Interprets `TASTY_IS_TEST` as `"1"` (sandbox) vs anything else (live).
   - Returns a valid `tastytrade.Session` instance that can be passed to `LiveMarketDataService`.

   This helper should be the **single source of truth** for streaming sessions used in the project.

2. **Wire Helper into LiveMarketDataService / CLI**

   - Identify where the CLI builds or configures the **streaming side** for live mode:
     - Search for:
       - `LiveMarketDataService`
       - `DXLink`
       - `tastytrade` imports in `stratdeck/cli.py` and related modules.
       - The string `"DXLink session creation failed"`.

   - Refactor that wiring to:

     - Call `make_tasty_streaming_session_from_env()` to get a `Session`.
     - Use that Session when constructing `LiveMarketDataService`.
     - Ensure that in **live data mode** (e.g. `STRATDECK_DATA_MODE=live`), the `TastyProvider` used by the planner has `live_quotes` set to this `LiveMarketDataService`.

   - Shutdown:
     - Ensure any `with`/context usage around `LiveMarketDataService` is correct so the loop/thread shuts down cleanly when CLI exits.

3. **Align Smoke Test (if needed)**

   - If appropriate, refactor `tests/live_quote_smoke.py` to use the new helper rather than duplicating session creation logic.
   - Behaviour must remain the same:
     - First line `source=None`, subsequent lines `source=dxlink`.
     - No async shutdown errors.

4. **Logging & Error Handling**

   - If DXLink session creation fails due to missing/invalid OAuth envs:
     - Log a clear warning or error:
       - Mention which envs are missing / invalid.
       - Avoid dumping full tokens or secrets into logs.
     - CLI should:
       - Either continue without streaming (REST-only) and make that explicit in a log.
       - Or abort with a clearly actionable error if live mode absolutely requires streaming for that run (use your judgement with minimal disruption).

   - Do **not** spam logs on each symbol; session-level errors should be logged once per CLI invocation.

5. **Tests**

   - Existing tests MUST remain green:

     ```bash
     python -m pytest
     ```

   - You may add a focused test (e.g. `tests/test_live_quotes_cli_integration.py` or small additions to `tests/test_tasty_provider_live_quotes.py`) that:

     - Mocks `make_tasty_streaming_session_from_env()` to return a fake object.
     - Asserts that the CLI live-mode wiring attempts to build `LiveMarketDataService` when `STRATDECK_DATA_MODE=live`.
     - Does **not** perform real network calls.

---

## WORKFLOW

1. **Read AGENTS.md** and follow repo conventions:
   - Logging style.
   - Code style (type hints, imports, error handling).
   - Testing and safety expectations.

2. **Inspect current streaming usage:**
   - `tests/live_quote_smoke.py`
   - `stratdeck/data/live_quotes.py`
   - `stratdeck/data/tasty_provider.py`
   - `stratdeck/cli.py` (and any helpers it relies on for live mode).

3. **Locate current DXLink/Session construction in CLI:**
   - Find where the message `DXLink session creation failed: ...` originates.
   - Understand what credentials / tokens it’s using now (likely old / wrong JWT).

4. **Implement session helper:**
   - Add `make_tasty_streaming_session_from_env()` (or similar) in an appropriate module under `stratdeck/data/`.
   - Use the same OAuth pattern that `tests/live_quote_smoke.py` currently uses.
   - Add minimal docstring and type hints.

5. **Refactor CLI live-mode wiring:**
   - Replace any ad-hoc Session/JWT logic for DXLink in CLI with the shared helper.
   - Ensure `LiveMarketDataService` is built and passed into `TastyProvider` in live data mode.
   - Ensure clean shutdown when CLI exits.

6. **Optionally align smoke test:**
   - Update `tests/live_quote_smoke.py` to call the shared helper instead of its own `make_session`, if that keeps things DRY without overcomplicating.

7. **Run tests and smoke checks:**
   - Python tests:

     ```bash
     python -m pytest
     ```

   - Manual smoke:

     ```bash
     export STRATDECK_DATA_MODE=live
     # ensure TASTY_CLIENT_SECRET / TASTY_REFRESH_TOKEN / TASTY_IS_TEST are set in env

     python -m stratdeck.cli trade-ideas        --strategy short_put_spread_index_45d        --universe index_core        --json-output
     ```

   - Confirm:
     - No `DXLink session creation failed: ... Invalid JWT` in logs.
     - No 429 spam for SPX/XSP underlyings (after stream warmup).
     - Underlying price hints for SPX/XSP are being served from DXLink (visible via debug logs or via small ad-hoc print/check if needed).

8. **Show diff:**

   At the end, print a concise summary and the `git diff` of all changes.

---

## ACCEPTANCE CRITERIA

- Running the CLI in live mode no longer logs:

  ```text
  DXLink session creation failed: ... invalid_grant ... Invalid JWT
  ```

  (unless env vars are genuinely misconfigured; in that case, logs must be clear and explicit.)

- For a correctly configured environment, when running:

  ```bash
  STRATDECK_DATA_MODE=live python -m stratdeck.cli trade-ideas ...
  ```

  the system:

  - Brings up a DXLink streaming session using OAuth envs.
  - Constructs `LiveMarketDataService` with that session.
  - Passes it into `TastyProvider` so `get_quote` returns DXLink-backed quotes (after initial warmup).
  - Avoids REST 429s for SPX/XSP in the steady state.

- All existing tests pass, and any new tests are stable and network-free.

- No regressions in non-live data modes.
