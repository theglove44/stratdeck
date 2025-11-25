# Codex-Max Task Spec – Align IVR with Tasty UI & Docs Update

## Project

- **Project**: StratDeck Agent System
- **Repo**: `git@github.com:theglove44/stratdeck.git`
- **Default branch**: `main`
- **Current feature branch**: _none assumed_ (Codex-Max should create its own feature branch)
- **Language**: Python 3.x
- **Tests**: `pytest -q`
- **CLI entrypoint**: `python -m stratdeck.cli ...`

This spec covers a small follow-up slice after `feature/live-ivr-rank`:

1. Align the StratDeck IVR value with the exact “IV Rank” number displayed in the Tasty watchlist UI.
2. Add brief docs on how to refresh the IV snapshot and how often to run it.

The assumption is that the live IVR pipeline implemented in `feature/live-ivr-rank` is already merged to `main` and working (i.e. we already fetch IVR from `/market-metrics` and wire it into trade-ideas).

---

## High-Level Goals

### Goal 1 – Align IVR with Tasty UI “IV Rank”

Right now, StratDeck’s IVR values come from Tasty’s `/market-metrics` endpoint and are normalised to 0–1. They **track** the shape of Tasty’s IV Rank, but they do **not** exactly match the watchlist “IV Rank” column values in the Tasty UI for all symbols.

Goal: Identify which exact field(s) from `/market-metrics` correspond to the watchlist “IV Rank”, and update StratDeck so that:

- The IVR value in StratDeck is numerically consistent with that field (modulo 0–1 vs 0–100 scaling).
- For a given symbol and timestamp, StratDeck’s IVR matches the Tasty UI IV Rank to within a small tolerance (e.g. ±1 point on a 0–100 scale).

### Goal 2 – Documentation / README Notes

Add concise documentation so that a future developer or operator understands:

- How to refresh the IV snapshot file from live data (`refresh-ivr-snapshot`).
- Recommended cadence (e.g. daily before the trading session, or whenever you want a fresh snapshot).

The docs should live alongside existing StratDeck docs (e.g. README and/or internal dev docs in `dev/` or `AGENTS.md`).

---

## Non-Goals

- No changes to POP, spread selection, or other strategy logic.
- No changes to watchlist universes or DXLink streaming symbols.
- No new agents or orchestrator behaviour.
- No automation/scheduling for snapshot refresh (just document the manual command).

---

## Relevant Existing Pieces

These exist after `feature/live-ivr-rank`:

- `stratdeck/data/market_metrics.py`
  - `fetch_iv_rank_for_symbols(symbols: Sequence[str], ...) -> Dict[str, float]`
  - `_extract_ivr_from_item(item)` – currently responsible for picking the rank field(s) and normalising to 0–1.

- `stratdeck/data/factory.py`
  - `_resolve_live_symbols()` – resolves the set of “live” symbols (static universes + tasty_watchlist universes).
  - `get_live_universe_symbols()` – public helper returning that symbol set.

- `stratdeck/tools/build_iv_snapshot.py`
  - `resolve_live_universe_symbols()` – wrapper around `get_live_universe_symbols()`.
  - `build_iv_snapshot()` – uses `fetch_iv_rank_for_symbols` to build `stratdeck/data/iv_snapshot.json` with shape `{SYMBOL: {"ivr": float_0_to_1}}`.

- `stratdeck/tools/scan_cache.py`
  - Attaches `ivr` to scan rows from the snapshot.

- `stratdeck/cli.py`
  - `refresh-ivr-snapshot` command:
    - Resolves live symbols.
    - Calls `build_iv_snapshot()`.
    - Prints how many symbols got IVR in the snapshot.

- Tests
  - `tests/data/test_market_metrics_ivrank.py`
  - `tests/tools/test_build_iv_snapshot.py`
  - `tests/tools/test_scan_cache.py`
  - `tests/test_data_factory_live_watchlist_symbols.py`
  - `tests/test_live_quotes_factory_session.py`

Codex-Max should **assume these files and tests exist on `main`** and build on them.

---

## Task 1 – Align IVR with Exact Tasty UI Field

### Objective

Make `stratdeck.data.market_metrics._extract_ivr_from_item` explicitly choose the same IV-rank field that backs the Tasty watchlist “IV Rank” column, and verify numerically that they match (within tolerance).

### Steps

#### 1.1 Create a small “debug” CLI for market-metrics

Add a new CLI command to `stratdeck/cli.py`, for example:

- `dump-market-metrics` (or similar).

Behaviour:

- Accepts `--symbols` argument: a comma-separated list of symbols, e.g. `--symbols AAPL,AMD,SPX`.
- Uses the **same** underlying `fetch_iv_rank_for_symbols` machinery, but also makes a direct `/market-metrics` call to retrieve and print the raw JSON for those symbols.

Implementation notes:

- Use the existing `fetch_iv_rank_for_symbols` function where possible.
- Add a thin helper in `market_metrics.py` that can fetch the raw payload for a given symbol list (reusing the same session and call shape), e.g.:
  - `fetch_market_metrics_raw(symbols: Sequence[str], ...) -> dict`
- The CLI should:
  - Resolve symbols from the CLI arg.
  - Call `fetch_market_metrics_raw` for those symbols.
  - Pretty-print a subset of the JSON to stdout (in a developer-friendly format, e.g. via `json.dumps(..., indent=2, sort_keys=True)`).

Testing:

- Add a unit test in `tests/data/test_market_metrics_ivrank.py` that uses a fake session to verify:
  - The CLI helper (`fetch_market_metrics_raw` or similar) calls the correct URL and params.
  - Raw payload is returned unchanged.

CLI behaviour can be smoke-tested manually; do not rely on real network in tests.

#### 1.2 Inspect fields for sample symbols

Goal: For a small set of symbols (e.g. `ETHA`, `GLD`, `AMD`, `SPY`, `SPX`), identify which field(s) in `items[*]` correspond to the Tasty UI “IV Rank” value.

Steps (manual, documented in comments / spec):
- Use the new CLI to dump market-metrics for a few symbols while visually comparing with the Tasty watchlist.
- Note which fields are present in the JSON:
  - e.g. `tw-implied-volatility-index-rank`, `implied-volatility-index-rank`, `tos-implied-volatility-index-rank`, `implied-volatility-index-rank-1y`, etc.
- Confirm which one matches the on-screen IV Rank column (within rounding). This is the **canonical field** that StratDeck IVR should use.

This inspection can be captured as comments in `market_metrics.py` above `_extract_ivr_from_item`, describing the chosen field and its observed behaviour.

#### 1.3 Update `_extract_ivr_from_item` to use the chosen field explicitly

Modify `_extract_ivr_from_item(item)` so that it:

1. **Prefers** the canonical field discovered in 1.2 (the one that matches Tasty UI IV Rank).
2. Optionally falls back to other rank-like fields only if the canonical field is missing, in a well-documented order.
3. Normalises the canonical value to 0–1 by:
   - Detecting whether the raw value is in 0–1 range or 0–100 range.
   - Scaling if necessary (e.g. divide by 100 if > 1.5 and <= 150).
4. Clamps/sanitises values to `[0.0, 1.0]`, discarding obviously bad values.

Update docstring to explicitly state:

- Which field(s) are used.
- That the result is a 0–1 value representing the same IV Rank as the Tasty UI.

#### 1.4 Strengthen tests for field selection

Extend `tests/data/test_market_metrics_ivrank.py`:

- Add test cases that simulate payloads containing multiple rank fields:
  - The canonical field with one value.
  - Secondary fields with different values.
- Assert that `_extract_ivr_from_item` returns the value derived from the canonical field, not from a fallback.
- Add a test that ensures 0–100 values are correctly scaled to 0–1 and match expectations.

Example patterns:

- Case: item has `canonical_field: 49.3` (representing 49.3%); assert `ivr == pytest.approx(0.493)`.
- Case: item has `canonical_field: 0.493`; assert `ivr == pytest.approx(0.493)`.
- Case: item missing canonical field but having an alternate `implied-volatility-index-rank` that looks 0–100; assert that the alternate is used (and scaled) in this fallback scenario.

#### 1.5 Manual verification checklist

No automated test can compare to the live Tasty UI, so codex-max should include a **manual verification checklist** in comments (or PR description):

1. Run the new CLI to dump raw metrics for a small set of symbols (e.g. ETHA, GLD, AMD, SPY, SPX).
2. Compare Tasty UI “IV Rank” column values to:
   - The canonical field in the JSON.
   - The StratDeck IVR (scaled to 0–100) reported in `trade-ideas` output.
3. Confirm differences are within an acceptable tolerance (e.g. ±1.0 point out of 100).

This checklist can be referenced in the PR description so the human operator can run it once before merging.

---

## Task 2 – Documentation / README Notes

### Objective

Add concise, discoverable documentation so that it’s clear:

- StratDeck supports a live IV snapshot for IVR.
- There is a CLI command to refresh it from Tasty.
- There is a recommended cadence for refreshing in a trading workflow.

### Steps

#### 2.1 Decide doc location

Two likely doc targets:

1. Top-level `README.md` (short, user-facing note).
2. Internal dev or ops doc, e.g.:
   - `dev/IVR-and-market-metrics.md` (new)
   - or a short section in an existing dev doc (e.g. `AGENTS.md` or `dev/architecture.md`).

Codex-Max should:

- Add a brief section to `README.md`.
- Add a slightly more detailed dev-focused section in a dedicated doc (either new file or existing dev doc).

#### 2.2 README addition

Add a short section to `README.md`, e.g. under a “Data Sources” or “IVR” heading:

Content should include:

- **What** IVR is used for in StratDeck (high level: scanning / filters / risk context).
- **Where** StratDeck gets IVR from:
  - Tasty `/market-metrics`, via `stratdeck.data.market_metrics`.
  - Normalised to 0–1.
- **How** to refresh the snapshot:
  - Example command:

    ```bash
    STRATDECK_DATA_MODE=live     python -m stratdeck.cli refresh-ivr-snapshot
    ```

- **Recommended cadence**:
  - E.g. “Run once per trading day before the open, or whenever you want to refresh IVR from Tasty.”

Keep it under a few paragraphs and one code block.

#### 2.3 Dev doc addition

Create or update a dev-facing doc, e.g. `dev/ivr-pipeline.md` (name up to codex-max, but it should be clear). Include:

- A brief description of the IVR pipeline:
  - Live source: Tasty `/market-metrics`.
  - Transformation: `_extract_ivr_from_item` → 0–1 IVR.
  - Storage: `stratdeck/data/iv_snapshot.json` with `{SYMBOL: {"ivr": float_0_to_1}}`.
  - Consumption:
    - `vol.load_snapshot()`.
    - `scan_cache.attach_ivr_to_scan_rows(...)`.
    - Trade planner / agents reading `ivr` from scan rows.
- **Operational notes**:
  - How to refresh snapshot (`refresh-ivr-snapshot`).
  - That snapshot uses the same live symbol universe as DXLink (`get_live_universe_symbols()`).
  - Any known limitations (e.g. if Tasty does not provide IVR for some symbols, they fall back to a neutral value and min_ivr filters are data-missing tolerant).

#### 2.4 Keep docs in sync with code

Ensure any CLI names and module paths mentioned in docs match actual code:

- If the debug CLI is called `dump-market-metrics`, document that exact command.
- If the canonical IVR field is, say, `tw-implied-volatility-index-rank`, mention that explicitly so future maintainers know what’s going on.

---

## Branch / Git Workflow

Codex-Max should follow this workflow:

1. **Create a new feature branch** from up-to-date `main`, e.g.:

   ```bash
   git checkout main
   git pull origin main
   git checkout -b feature/ivr-align-and-docs
   ```

2. Implement the changes described in Tasks 1 & 2:
   - Update / add Python modules and tests.
   - Update `README.md` and dev docs.

3. Run tests:

   ```bash
   pytest -q
   ```

4. Optionally do a manual smoke-test:

   ```bash
   export STRATDECK_DATA_MODE=live

   python -m stratdeck.cli refresh-ivr-snapshot

   python -m stratdeck.cli trade-ideas      --universe tasty_watchlist_chris_historical_trades      --strategy short_put_spread_equity_45d      --json-output > /tmp/ideas_live_ivr.json

   jq '.[0:20] | map({symbol, ivr})' /tmp/ideas_live_ivr.json
   ```

5. Commit with a clear message, e.g.:

   ```bash
   git commit -am "Align IV Rank with Tasty UI field and document IVR snapshot workflow"
   ```

6. Push branch and open a PR to `main`:

   ```bash
   git push -u origin feature/ivr-align-and-docs
   ```

PR description should reference:

- The canonical IVR field chosen.
- Any manual verification done (symbols checked, diffs observed).
- New docs added and where they live.

---

## Acceptance Criteria

- `pytest -q` passes with no failures.
- There is a CLI or helper that can dump raw `/market-metrics` JSON for a specified symbol list (used for debugging, not necessarily part of the public user interface).
- `_extract_ivr_from_item` uses a clearly documented canonical field from `/market-metrics` that matches the Tasty UI “IV Rank” column (within a small rounding tolerance).
- At least one test explicitly verifies field precedence logic (canonical vs fallbacks).
- README contains a short, accurate description of:
  - IVR source.
  - `refresh-ivr-snapshot` usage.
  - Recommended refresh cadence.
- Dev docs (existing or new) briefly describe:
  - The IVR pipeline (source → snapshot → consumers).
  - Operational notes for refreshing the snapshot and any known limitations.
- Manual verification (described in PR or comments) has been performed on a small set of symbols, confirming that StratDeck’s IVR agrees with Tasty UI IV Rank within acceptable tolerance.
