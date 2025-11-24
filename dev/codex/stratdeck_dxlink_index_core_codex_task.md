# Codex-Max Task: DXLink Subscriptions for `index_core` Universe (Including XSP)

## Summary

The live data refactor has successfully:

- Made `TastyProvider.get_quote` **DXLink-first** with a throttled REST fallback.
- Eliminated previous HTTP 429 issues by caching/throttling REST quotes.
- Confirmed **DXLink streaming works for SPX** in live mode.

Current live checks show:

```text
SPX mid= 6607.3 source= dxlink
XSP mid=  660.3 source= rest-fallback
```

`LiveMarketDataService` is running and does produce DXLink snapshots for **SPX**, but **never for XSP**. As a result, `TastyProvider.get_quote("XSP")` always falls back to the (throttled) REST path.

This task completes the streaming integration by:

- Ensuring `LiveMarketDataService` subscribes to the full **`index_core` universe** (at minimum, SPX and XSP).
- Making sure any symbol in that universe uses DXLink snapshots once available.
- Locking in the behaviour with focused tests.

The aim: **for all `index_core` symbols (SPX, XSP, etc.), live quotes come from DXLink whenever snapshots exist, with REST only as a fallback.**

---

## Repository & Context

- **GitHub repo:** `theglove44/stratdeck`
- **Local folder (typical):** `~/Projects/stratdeck-copilot`
- **Default branch:** `main`
- **Existing feature work:** `feature/live-quotes-dxlink-429-fix` (or equivalent, already merged or pending).

This task is a **follow-up slice**; it assumes that:

- `TastyProvider` already:
  - Uses DXLink snapshots first.
  - Provides `source` tagging (`"dxlink"` vs `"rest-fallback"`).
  - Implements per-symbol REST throttling with `STRATDECK_QUOTE_CACHE_TTL`.
- `LiveMarketDataService` already:
  - Connects to DXLink.
  - Produces snapshots for SPX in live mode.
- All tests currently pass (`pytest -q`).

---

## High-Level Goals

1. **DXLink streaming must be active for the entire `index_core` universe**, not just SPX.
   - At minimum: SPX and XSP.
   - Preferably: whatever symbols are defined in the `index_core` universe config.

2. **Any symbol with a DXLink snapshot must yield `source="dxlink"`** from `TastyProvider.get_quote`.
   - No symbol in `index_core` should be “permanently REST-only” in live mode.

3. **Factory wiring must ensure `LiveMarketDataService` knows which symbols to subscribe to** based on the active universe.

4. **Tests must verify:**
   - Universe subscription logic includes XSP (and other index_core symbols).
   - Provider uses DXLink for XSP once a snapshot is present.

5. **All existing tests must remain green** (no regressions).

---

## Files in Scope

Inspect and potentially modify:

- `stratdeck/data/live_quotes.py`
- `stratdeck/data/factory.py`

You may also need to **add or adapt tests** in:

- `tests/test_tasty_provider_live_quotes.py`
- `tests/test_live_market_data_service.py`
- Any other tests related to:
  - Live universes/universe assignment (if present).
  - Live data wiring.

---

## Current Observed Behaviour (Live Mode)

Commands already executed in the real environment:

```bash
export STRATDECK_DATA_MODE=live

python - << 'PY'
from stratdeck.data.factory import get_provider

p = get_provider()
for s in ["SPX", "XSP"]:
    q = p.get_quote(s)
    print(s, "mid=", q.get("mid"), "source=", q.get("source"))
PY
```

Result:

```text
SPX mid= 6607.3 source= dxlink
XSP mid=  660.3 source= rest-fallback
```

Snapshot probe:

```bash
python - << 'PY'
import time
from stratdeck.data.factory import get_provider

p = get_provider()
lq = getattr(p, "_live_quotes", None)
print("Live quotes object:", lq)

for i in range(5):
    snap_xsp = lq.get_snapshot("XSP") if lq else None
    snap_spx = lq.get_snapshot("SPX") if lq else None
    print(f"tick {i}: XSP={snap_xsp}  SPX={snap_spx}")
    time.sleep(1)
PY
```

Representative output:

```text
Live quotes object: <LiveMarketDataService ...>
tick 0: XSP=None  SPX=None
tick 1: XSP=None  SPX=QuoteSnapshot(...)
tick 2: XSP=None  SPX=QuoteSnapshot(...)
tick 3: XSP=None  SPX=QuoteSnapshot(...)
tick 4: XSP=None  SPX=None
```

Interpretation:

- **SPX** is subscribed on DXLink and producing snapshots.
- **XSP** is **never** producing snapshots.
- Therefore, provider’s DXLink-first logic never triggers for XSP and always uses REST (throttled).

---

## Desired Behaviour

### 1. Universe-based DXLink Subscription

- `LiveMarketDataService` must be able to **subscribe to a list of symbols** (e.g. `["SPX", "XSP"]`).
- `factory.get_provider()` (or equivalent) must:
  - Resolve the relevant universe in live mode (e.g. `index_core`).
  - Pass that symbol list into `LiveMarketDataService` so those symbols are subscribed on DXLink startup.

The end result:

- In live mode, for any symbol in `index_core`:
  - `LiveMarketDataService.get_snapshot(symbol)` will eventually yield a non-None snapshot.
  - `TastyProvider.get_quote(symbol)` will then report `source="dxlink"` once the stream has warmed up.

### 2. Graceful Fallbacks

The existing behaviour should remain unchanged:

- If **no DXLink snapshot is available yet** for a symbol:
  - Provider may wait briefly, then use throttled REST fallback.
- REST fallback continues to be:
  - Tagged as `source="rest-fallback"`.
  - Protected by `STRATDECK_QUOTE_CACHE_TTL` to avoid 429s.

---

## Detailed Implementation Requirements

### A. Enhance `LiveMarketDataService` to accept and manage subscriptions

In `stratdeck/data/live_quotes.py`:

1. Ensure the service has a clear API for symbol subscriptions, for example:

   ```python
   class LiveMarketDataService:
       def __init__(self, session, ...):
           ...
           self._symbols = set()
           self._started = False

       def start(self, symbols: list[str]) -> None:
           # idempotent: safe to call multiple times
           self._symbols.update(sym.upper() for sym in symbols)
           if not self._started:
               # start background loop / tasks
               self._started = True
           # ensure streamer is actually subscribed to self._symbols
   ```

   or, if you already have something similar, adapt it so:

   - It can receive a full universe list (not just a hard-coded SPX).
   - It ensures those symbols are subscribed on DXLink.

2. Ensure the DXLink streaming loop:

   - Subscribes to **all** symbols in `_symbols` on startup.
   - Handles any dynamic additions to `_symbols` (if supported).
   - Updates an internal snapshot map (`symbol -> QuoteSnapshot`).

3. Preserve existing behaviour for SPX.

   - Don’t break the working SPX path.
   - Extend the same logic to XSP and any other `index_core` symbols.

### B. Wire universe-based subscriptions from `factory.get_provider()`

In `stratdeck/data/factory.py`:

1. Find the branch where `STRATDECK_DATA_MODE=live` selects the live provider.

2. Introduce logic to determine the live symbol universe, e.g.:

   - If there is a central universe config (e.g. `strategies.yaml` with `index_core`):
     - Use that to obtain the list of symbols.
   - As a minimum, hard-code SPX and XSP for now if universe access is non-trivial:

     ```python
     index_core_symbols = ["SPX", "XSP"]
     ```

3. After constructing `LiveMarketDataService`, ensure it is **started and subscribed** with that universe:

   ```python
   live_quotes = LiveMarketDataService.from_env(...)  # or whatever constructor you use
   live_quotes.start(index_core_symbols)  # or subscribe(index_core_symbols)
   provider = TastyProvider(live_quotes=live_quotes)
   ```

4. Confirm this wiring path is what `get_provider()` uses for:

   - `trade-ideas` CLI in live mode.
   - Any other live-mode commands that rely on quotes.

---

## Testing Requirements

### 1. Unit / Integration-like Tests for Subscription Behaviour

Update or add tests in:

- `tests/test_live_market_data_service.py`
- `tests/test_tasty_provider_live_quotes.py`

#### a) LiveMarketDataService subscriptions

Add tests that:

- Instantiate a `LiveMarketDataService` with a **fake** or stub DXLink backend.
- Call `start(["SPX", "XSP"])`.
- Simulate incoming quote events for:
  - SPX
  - XSP
- Assert that:
  - `get_snapshot("SPX")` returns the SPX snapshot.
  - `get_snapshot("XSP")` returns the XSP snapshot.

You can mock/stub the underlying DXLink client so no real network is involved.

#### b) Provider behaviour with XSP snapshot present

Extend `tests/test_tasty_provider_live_quotes.py` to include:

- A fake `LiveMarketDataService` where `get_snapshot("XSP")` returns a non-None snapshot with a valid `mid`.
- Inject this fake into `TastyProvider`.
- Assert that:

  ```python
  q = provider.get_quote("XSP")
  assert q["source"] == "dxlink"
  assert q["mid"] == expected_mid
  ```

This proves that once snapshots exist, **provider treats XSP exactly like SPX**.

### 2. No Real DXLink / Tasty Calls in Tests

- All tests must remain network-free.
- Use fakes/mocks for:
  - DXLink streaming.
  - Any underlying Tasty session behaviour.

---

## Manual Live Verification (Optional but Recommended)

Once tests are green, Codex-Max should provide you with a small manual verification snippet (to be run by you, not by Codex) of the form:

```bash
export STRATDECK_DATA_MODE=live

python - << 'PY'
from stratdeck.data.factory import get_provider

p = get_provider()
for s in ["SPX", "XSP"]:
    q = p.get_quote(s)
    print(s, "mid=", q.get("mid"), "source=", q.get("source"))
PY
```

The expected live behaviour:

- `SPX ... source= dxlink`
- `XSP ... source= dxlink` (after the stream has warmed up)

If XSP still shows `source= rest-fallback` in your live environment after this change, it indicates remaining issues in:

- Universe wiring, or
- LiveMarketDataService subscriptions.

---

## Commands to Run

From repo root:

### 1. Install (if needed)

```bash
pip install -e ".[dev]"
```

### 2. Run the full test suite

```bash
pytest -q
```

All tests must pass before and after your modifications.

---

## Workflow Expectations for Codex-Max

1. **Sync**

   - Ensure the working branch is up-to-date with `main` (or with your live-quotes feature branch, depending on where this work lands).

2. **Analyse**

   - Review:
     - `stratdeck/data/live_quotes.py`
     - `stratdeck/data/factory.py`
     - Existing tests in:
       - `tests/test_live_market_data_service.py`
       - `tests/test_tasty_provider_live_quotes.py`

3. **Implement**

   - Add/extend symbol subscription handling in `LiveMarketDataService`.
   - Wire universe symbols (at minimum SPX + XSP) from factory into live_quotes.
   - Ensure idempotent `start`/`subscribe` semantics.

4. **Test**

   - Run `pytest -q`.
   - If any tests fail, iterate on code/tests until green.

5. **Summarise**

   - Output:
     - The changes made.
     - Any new tests added.
     - Confirmation that `pytest -q` passes.
     - A short “manual verification” snippet for you to run in live mode (`get_quote("SPX")` and `get_quote("XSP")` as above).

> If Codex-Max cannot perform git/MCP operations (permissions, read-only filesystem, etc.), it should still:
> - Make all code and test changes locally in the MCP workspace.
> - Run `pytest -q` until green.
> - Provide copy-pasteable git commands for you to create a branch, commit, and push the changes manually.

---

## Done Criteria

Codex-Max is finished with this task when:

1. **Implementation:**
   - `LiveMarketDataService` accepts a list of symbols and subscribes them on DXLink startup.
   - `factory.get_provider()` in live mode passes the `index_core` universe (at least SPX + XSP) into `LiveMarketDataService`.

2. **Behaviour (testable with fakes):**
   - Any symbol with a DXLink snapshot (including XSP) results in `source="dxlink"` from `TastyProvider.get_quote`.

3. **Testing:**
   - `pytest -q` runs successfully with all tests passing.
   - New tests assert subscription and DXLink usage for at least XSP and SPX.

4. **Manual verification snippet:**
   - Codex-Max provides a short command snippet (as above) to confirm, in your real environment, that both SPX and XSP now show `source="dxlink"` after DXLink has warmed up.
