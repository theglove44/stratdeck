# Codex-Max Task: Tasty Watchlist–Backed Universe for StratDeck (and DXLink Symbol Wiring)

## Summary

We want StratDeck to use a **Tasty account watchlist** as a dynamic symbol universe, instead of hardcoding stock tickers in code/config.

This task will:

1. Add support for a **`tasty_watchlist` universe type** in StratDeck’s config/model layer.
2. Implement a small helper module to **fetch symbols from a named Tasty watchlist** via the existing Tasty REST session.
3. Wire that watchlist-backed universe into:
   - The **strategy engine** (e.g. `trade-ideas --universe tasty_watchlist_stratdeck`),
   - The **live data factory** so DXLink (and, where unavailable, REST) is set up for the whole universe.
4. Add tests and run `pytest -q` to ensure no regressions.

> **Important:** Don’t rework the DXLink core logic again. Assume the existing streaming + REST throttling contracts are correct and tested. This task is only about *how the symbol list is populated* and passed into the live data/strategy layers.

---

## Repository & Branch Workflow

- **GitHub repo (correct, do NOT change):**  
  `git@github.com:theglove44/stratdeck.git`

- **Default branch:** `main`

- **Feature branch for this work:**  
  `feature/tasty-watchlist-universe`

### Expected Git / MCP Workflow

1. **Clone or open the repo**

   - If the GitHub MCP server can clone, use:
     - Repo SSH URL: `git@github.com:theglove44/stratdeck.git`
   - Local checkout directory name can be `stratdeck` or `stratdeck-copilot`; the directory name doesn’t matter, the **remote URL does**.

2. **Create feature branch**

   From an up-to-date `main`:

   ```bash
   git checkout main
   git pull origin main
   git checkout -b feature/tasty-watchlist-universe
   ```

3. **Implement changes & iterate with tests** (see Testing section).

4. **Commit and push**

   Once tests are green:

   ```bash
   git add .
   git commit -m "Add Tasty watchlist-backed universe and DXLink symbol wiring"
   git push -u origin feature/tasty-watchlist-universe
   ```

5. **PR**

   Open a PR from `feature/tasty-watchlist-universe` into `main` with a summary of changes and test results.

> If the MCP environment cannot perform git operations (403/404 from GitHub, read-only filesystem, lock errors, etc.), it must:
> - Still apply all code + test changes locally in its workspace.
> - Run `pytest -q` until green.
> - Output **explicit, copy-pasteable** git commands (like the above) for the user to run manually on their own machine.

---

## Current Context / Assumptions

You should assume the following is already true in `main` (or will be by the time this branch is used):

- There is a working Tasty integration for:
  - REST quotes (with **per-symbol throttling** via `STRATDECK_QUOTE_CACHE_TTL`).
  - DXLink streaming for at least some symbols (e.g. SPX) via `LiveMarketDataService`.
- `LiveMarketDataService` is instantiated in `stratdeck/data/factory.py` in live mode and can accept a symbol list.
- `TastyProvider` supports a **DXLink-first, REST-fallback** quote policy, with `source` tagging (`"dxlink"` vs `"rest-fallback"`).
- The strategy/universe config is defined in YAML (e.g. `stratdeck/config/strategies.yaml`), and there is already at least one universe, such as `index_core`.

This task should **not** fundamentally change those behaviours; it should only expand where the **symbol list** comes from and how it flows through the system.

---

## High-Level Goals

1. Introduce a **`tasty_watchlist`-backed universe** that draws its symbols from a named Tasty account watchlist (e.g. `StratDeckUniverse`).
2. Make it possible to run:

   ```bash
   python -m stratdeck.cli trade-ideas      --universe tasty_watchlist_stratdeck      --strategy <some_strategy>      --json-output
   ```

   and have the strategy engine use the **dynamic symbol list from that watchlist**.

3. Ensure the **live data factory** (DXLink + REST) subscribes to the **union** of:
   - `index_core` (SPX/XSP), and
   - the Tasty watchlist universe.

4. Add tests to:
   - Validate watchlist symbol extraction logic.
   - Validate universe resolution for `tasty_watchlist` kind.
   - Validate that factory wiring builds a `LiveMarketDataService` with the expected symbol set (using mocks, no real network).

5. Keep **all existing tests green** via `pytest -q`.

---

## Design Details

### 1. New helper: `stratdeck/data/tasty_watchlists.py`

Create a new module:

```python
# stratdeck/data/tasty_watchlists.py

from typing import List

from .tasty_provider import make_tasty_session_from_env  # or equivalent helper you already use


def get_watchlist_symbols(name: str) -> List[str]:
    '''
    Return the *underlying* symbols from the given Tasty watchlist name.

    - Uses the existing Tasty auth/session helper (no duplication of login code).
    - Calls the documented watchlist endpoint for the current user.
    - Extracts and normalises symbol strings (e.g. 'AAPL', 'SPX', 'XSP').
    - De-duplicates and sorts the result.
    '''
    ...
```

Implementation notes:

- Reuse the existing Tasty session builder that the rest of StratDeck uses (e.g. `make_tasty_session_from_env` or similar).
- Call the Tasty watchlist endpoint for the **current user**.
- For each instrument, extract a suitable **underlying symbol**:
  - For stocks/ETFs: the symbol itself (e.g. `"AAPL"`, `"GLD"`).
  - For options/futures: the underlying symbol (e.g. `"SPX"` from an SPX option).
- Return a **sorted, unique** list of symbols (`List[str]`).

This function must not hit any live network in tests; all HTTP/Tasty calls must be mocked in tests.

---

### 2. Universe config: `tasty_watchlist` kind

In the universe/strategy config layer (likely `stratdeck/strategies.py` and `stratdeck/config/strategies.yaml`):

1. **Extend the universe model** to support a new kind:

   - Add a discriminator/field like `kind: Literal["static", "index_core", "tasty_watchlist", ...]`.
   - For `tasty_watchlist`, include:
     - `watchlist_name: str` – exact name of the watchlist in the Tasty account.

2. **Example YAML config** addition:

   In `stratdeck/config/strategies.yaml` (or equivalent):

   ```yaml
   universes:
     index_core:
       kind: static
       symbols:
         - SPX
         - XSP

     tasty_watchlist_stratdeck:
       kind: tasty_watchlist
       watchlist_name: StratDeckUniverse
   ```

   - `tasty_watchlist_stratdeck` is an example name for the new universe.
   - `StratDeckUniverse` is the name of the watchlist in the Tasty UI.

3. **Universe resolution logic**

   In the code that resolves a universe from name → symbol list (e.g. `build_strategy_universe_assignments` / similar):

   - For `kind == "static"` (existing behaviour): use the `symbols` list as-is.
   - For `kind == "tasty_watchlist"`:
     - Call `get_watchlist_symbols(watchlist_name)`.
     - Use the returned list as the universe for that universe name.

   Make sure this resolution layer is **pure Python** and easy to test, with Tasty network calls **mocked**.

---

### 3. DXLink symbol wiring: union of `index_core` and watchlist universe

In `stratdeck/data/factory.py`, there is already logic to build a live quote service for DXLink in live mode, something like:

```python
def _build_live_quotes():
    # Creates LiveMarketDataService with a session and a symbol list
    # Registers stop() with atexit
    ...
```

Extend this to:

1. **Collect base symbols** for live streaming:

   - Always include `index_core` (SPX, XSP).
   - If the `tasty_watchlist_stratdeck` universe exists (or any universe of kind `tasty_watchlist`), include those symbols too.

   Pseudocode:

   ```python
   def _resolve_live_symbols() -> list[str]:
       # start with index_core
       symbols = set(resolve_universe_symbols("index_core"))

       # add any tasty_watchlist universes that are configured
       for universe in all_configured_universes():
           if universe.kind == "tasty_watchlist":
               symbols |= set(resolve_universe_symbols(universe.name))

       return sorted(symbols)
   ```

   Note:
   - `all_configured_universes()` can be an existing helper or something you add.
   - `resolve_universe_symbols(name)` should NOT make network calls for non-watchlist universes.

2. **Pass this symbol set into `LiveMarketDataService`**

   Use the existing constructor style:

   ```python
   symbols = _resolve_live_symbols()
   session = make_tasty_streaming_session_from_env()
   service = LiveMarketDataService(session, symbols)
   service.start()
   if hasattr(service, "stop"):
       atexit.register(service.stop)
   ```

   - Do **not** change the public API of `LiveMarketDataService` unless strictly necessary.
   - Do **not** rework DXLink internals; just change the `symbols` list you feed in.

3. **Failure / fallback**

   - If anything in `_resolve_live_symbols()` fails (config error, watchlist fetch error) you can:
     - Log a warning.
     - Fall back to a minimal static set (e.g. just `["SPX", "XSP"]`).
   - `TastyProvider` must still be able to operate with a partial symbol set; REST fallback remains in place.

---

## Testing Requirements

All testing must be network-free (no real calls to Tasty / DXLink).

### 1. `get_watchlist_symbols` tests

Create tests in a new module, e.g.:

- `tests/test_tasty_watchlists.py`

Tests should:

- Monkeypatch/mocks the Tasty session + HTTP response from the watchlist endpoint.
- Provide a fake JSON/response body with a few instruments:
  - 2–3 stocks (AAPL, MSFT).
  - Maybe 1–2 index or ETF symbols.
  - Option instruments whose underlying is SPX, etc.
- Assert that:
  - `get_watchlist_symbols("SomeName")` returns a **sorted unique** list like:
    `["AAPL", "GLD", "MSFT", "SPX"]`.
  - Underlying symbol extraction behaves as expected.

### 2. Universe resolution tests

In existing universe/strategy tests (or a new module):

- Add tests that define in-memory config objects that include:

  ```python
  tasty_universe = UniverseConfig(
      name="tasty_watchlist_stratdeck",
      kind="tasty_watchlist",
      watchlist_name="StratDeckUniverse",
  )
  ```

- Monkeypatch `get_watchlist_symbols("StratDeckUniverse")` to return `["AAPL", "MSFT"]`.
- Assert that resolving the universe `tasty_watchlist_stratdeck` yields `{"AAPL", "MSFT"}` as symbols.
- Assert that existing, non-watchlist universes (e.g. `index_core`) are unchanged.

### 3. DXLink symbol wiring tests (factory)

Extend or add tests like:

- `tests/test_data_factory_live_watchlist_symbols.py` (or extend existing `tests/test_data_factory_live_quotes.py`).

Plan:

- Monkeypatch:
  - Universe config loader to return a config with:
    - `index_core`: `["SPX", "XSP"]`
    - `tasty_watchlist_stratdeck`: kind `tasty_watchlist`, watchlist_name `StratDeckUniverse`
  - `get_watchlist_symbols("StratDeckUniverse")` to return `["AAPL", "MSFT"]`.
  - `LiveMarketDataService` with a dummy implementation that records the `symbols` it was constructed with.
- Call `_build_live_quotes()`.
- Assert that:
  - The dummy `LiveMarketDataService` was created with a `symbols` list containing `{"SPX", "XSP", "AAPL", "MSFT"}` (order not important, but you can check sorted).
  - `start()` was called on the dummy service.
  - `_live_quotes_instance` is cached and subsequent calls reuse it.

### 4. Full suite

Finally, run:

```bash
pytest -q
```

- All existing tests must remain green.
- The new tests must pass.

If any tests fail, iterate on the implementation and tests until `pytest -q` passes fully.

---

## Manual Verification (Post-merge, run by the user)

Codex-Max **must not** attempt live network verification itself, but should provide the user with a short snippet they can run locally to confirm behaviour once the branch is merged and deployed.

For example:

```bash
export STRATDECK_DATA_MODE=live

python - << 'PY'
from stratdeck.data.factory import get_provider

SYMS = ["SPX", "XSP", "AAPL", "AMD", "AMZN", "GLD", "MSFT", "TSLA"]

p = get_provider()
for s in SYMS:
    q = p.get_quote(s)
    print(f"{s:5} mid={q.get('mid')}  source={q.get('source')}")
PY
```

Expected real-world behaviour:

- For symbols in universes wired to DXLink:
  - Either `source="dxlink"` (if DXLink provides snapshots), or
  - `source="rest-fallback"` if DXLink doesn’t support that symbol / entitlement.
- REST calls should still be throttled per symbol as implemented previously.

---

## Done Criteria

Codex-Max is finished with this task when:

1. **Implementation:**
   - `stratdeck/data/tasty_watchlists.py` implements `get_watchlist_symbols(name: str)`.
   - `Universe` / config models support `kind: "tasty_watchlist"` with a `watchlist_name`.
   - Universe resolution:
     - Uses `get_watchlist_symbols` to build symbol lists for watchlist-backed universes.
   - `stratdeck/data/factory.py`:
     - Resolves live symbols as the union of `index_core` and all `tasty_watchlist` universes.
     - Constructs `LiveMarketDataService(session, symbols)` and calls `start()`.
     - Registers `stop` with `atexit` where available.

2. **Testing:**
   - New tests exist for:
     - Watchlist symbol extraction.
     - `tasty_watchlist` universe resolution.
     - Factory symbol union and LiveMarketDataService construction.
   - `pytest -q` passes with **no** failing tests.

3. **Git state (best-effort in MCP; explicit instructions if git is unavailable):**
   - Feature branch: `feature/tasty-watchlist-universe`.
   - All changes committed with a descriptive message.
   - Branch pushed to `git@github.com:theglove44/stratdeck.git`.
   - Clear instructions are output for opening a PR into `main`.

If git operations cannot be performed in the MCP environment, the final response must clearly list:

- Which files were modified/created.
- The exact shell commands the user should run locally to:
  - Create the branch,
  - Stage the files,
  - Commit,
  - Push,
  - And open the PR in GitHub.
