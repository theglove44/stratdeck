# Codex-Max Task — IVR Alignment Recheck & Self-Correcting Tests

## Overview

You are working in the **StratDeck Copilot** repo:

- **Repo:** `git@github.com:theglove44/stratdeck-copilot.git`
- **Language:** Python 3.x
- **Test runner:** `pytest -q`
- **CLI entrypoint:** `python -m stratdeck.cli ...`

This task focuses *only* on the **IV Rank (IVR) pipeline alignment** with the Tastytrade platform. The goal is:

1. To **lock down** the IVR extraction logic from `/market-metrics` with unit tests.
2. To provide a **debug CLI tool** that shows, for a given symbol, the raw IVR-related fields and the final extracted IVR.
3. To create a **repeatable testing flow** that allows the human user to compare StratDeck IVR vs Tasty UI IV Rank and adjust the logic if needed.
4. To ensure the IVR pipeline is **self-correcting**: any future change to the extraction logic is caught by tests using captured fixtures.

**Non-goals for this task:**

- No changes to the filters engine or `TradePlanner` (that was the previous slice).
- No changes to orchestrator, agents, or order placement.
- No new data sources; continue using the existing Tastytrade `/market-metrics` integration.
- No UI/ranking changes; we just want IVR values that are consistent and observable.

---

## Existing IVR Pipeline (Context)

The IVR flow currently looks like this:

### 1. Raw market metrics fetch

- Module: `stratdeck/data/market_metrics.py`

Key elements:

- `fetch_market_metrics_raw(symbols, session=None, chunk_size=50)`  
  - Issues `/market-metrics` GET requests for a list of symbols (chunked).
  - Combines responses into a single `{"data": {"items": [...]}}` payload.

- `_extract_ivr_from_item(item)`  
  - Given a single `item` from the `items` list, extracts IV Rank.
  - Uses canonical fields like:
    - `implied-volatility-index-rank`
    - `tw-implied-volatility-index-rank`
    - and TOS-style fallbacks (e.g. `tos-implied-volatility-index-rank`).
  - Applies heuristics to auto-scale values from 0–100 to 0–1 if necessary.
  - Clamps IVR to `[0.0, 1.0]`.

- `fetch_iv_rank_for_symbols(symbols, session=None)`  
  - Uses `fetch_market_metrics_raw` and `_extract_ivr_from_item`.
  - Returns a mapping `{symbol: ivr_float_0_to_1}`.

### 2. IV Snapshot writer

- Module: `stratdeck/tools/build_iv_snapshot.py`

Key elements:

- `build_iv_snapshot()`
  - Resolves a live universe of symbols (via `resolve_live_universe_symbols` and `data.factory.get_live_universe_symbols`).
  - Calls `fetch_iv_rank_for_symbols` to get `{symbol: ivr}`.
  - Writes the IVR data to `stratdeck/data/iv_snapshot.json` as an atomic file:
    - Uses a temp file then rename.
    - JSON sorted keys.
    - Values are **0–1 floats** representing IVR fraction (not percent).

### 3. Snapshot loader and trade-ideas wiring

- Module: `stratdeck/tools/vol.py`

Key elements:

- `load_snapshot(path=None)`
  - Reads `iv_snapshot.json` (default `stratdeck/data/iv_snapshot.json`).
  - Accepts either `{SYM: {"ivr": x}}` or `{SYM: x}`.
  - Returns `{SYM: float}` (0–1 floats).

- The trade-ideas pipeline (`stratdeck/cli.py` and `stratdeck/tools/scan_cache.py`):
  - `load_snapshot(str(IV_SNAPSHOT_PATH))` to get `{SYM: ivr_float}`.
  - `attach_ivr_to_scan_rows(...)` adds `ivr` to scan rows before they are fed to `TradePlanner`.
  - Scan rows are also cached (`store_scan_rows`).

At a high level, this pipeline is **already working correctly for some symbols** (e.g., IVR for SPX and AAPL closely match the Tasty watchlist IV Rank). Other symbols may show modest discrepancies (e.g., a few IVR points difference).

This task is about exposing, testing, and tightening that pipeline.

---

## Branch & Task Setup

You will work on a **dedicated feature branch** for this slice:

- **Branch name:** `feature/ivr-alignment-recheck`

### Step 1 — Create / update the branch

From the project root on the developer machine:

```bash
cd /Users/christaylor/Projects/stratdeck-copilot

# Ensure main is up to date
git checkout main
git pull --ff-only origin main

# Create the feature branch
git checkout -b feature/ivr-alignment-recheck
```

All changes for this IVR slice must be made on this branch.

---

## Task 1 — Unit Tests for `_extract_ivr_from_item`

**Goal:** Lock in the expected behaviour of IVR extraction from raw `/market-metrics` items.

### 1.1 Add a test module

Create a new test file:

- `tests/test_market_metrics_ivr_extraction.py`

In this file, import `_extract_ivr_from_item` from `stratdeck.data.market_metrics` and write tests against synthetic `item` dicts.

Examples to cover:

1. **Canonical 0–1 value (already normalised)**

   ```python
   def test_extract_ivr_from_item_canonical_fraction():
       item = {"symbol": "SPX", "implied-volatility-index-rank": 0.15}
       ivr = _extract_ivr_from_item(item)
       assert ivr == pytest.approx(0.15)
   ```

2. **Canonical 0–100 value (percent)**

   ```python
   def test_extract_ivr_from_item_canonical_percent():
       item = {"symbol": "SPX", "implied-volatility-index-rank": 15.0}
       ivr = _extract_ivr_from_item(item)
       assert ivr == pytest.approx(0.15)
   ```

3. **Fallback TOS-style field**

   ```python
   def test_extract_ivr_from_tos_field():
       item = {"symbol": "SPX", "tos-implied-volatility-index-rank": 27.0}
       ivr = _extract_ivr_from_item(item)
       assert ivr == pytest.approx(0.27)
   ```

4. **Clamping for out-of-range values**

   ```python
   def test_extract_ivr_clamps_high_values():
       item = {"symbol": "SPX", "implied-volatility-index-rank": 180.0}
       ivr = _extract_ivr_from_item(item)
       assert 0.99 <= ivr <= 1.0

   def test_extract_ivr_clamps_negative_values():
       item = {"symbol": "SPX", "implied-volatility-index-rank": -5.0}
       ivr = _extract_ivr_from_item(item)
       assert ivr == 0.0
   ```

5. **Missing / non-numeric**

   ```python
   def test_extract_ivr_missing_field_returns_none():
       item = {"symbol": "SPX"}
       ivr = _extract_ivr_from_item(item)
       assert ivr is None

   def test_extract_ivr_non_numeric_returns_none():
       item = {"symbol": "SPX", "implied-volatility-index-rank": "n/a"}
       ivr = _extract_ivr_from_item(item)
       assert ivr is None
   ```

Constraints:

- No network calls; use pure synthetic data.
- If `_extract_ivr_from_item` is currently private (leading underscore), import it directly in tests anyway (this repo already treats it as a debug helper).

After implementing these tests, run:

```bash
pytest -q tests/test_market_metrics_ivr_extraction.py
```

Fix any failures by adjusting either the tests (to match the intended behaviour) or `_extract_ivr_from_item` (if the current behaviour is clearly wrong).

---

## Task 2 — IVR Debug CLI Command

**Goal:** Provide a simple CLI tool to inspect IVR-related fields from `/market-metrics` for one or more symbols, and show how `_extract_ivr_from_item` interprets them.

### 2.1 Add a new CLI subcommand: `ivr-debug`

In `stratdeck/cli.py`, add a new typer subcommand, for example:

```python
@app.command()
def ivr_debug(
    symbols: str = typer.Argument(..., help="Comma-separated list of symbols"),
) -> None:
    """
    Debug IV Rank for one or more symbols by comparing raw market-metrics
    fields and the extracted IVR used by StratDeck.
    """
    from stratdeck.data.market_metrics import (
        fetch_market_metrics_raw,
        _extract_ivr_from_item,
    )

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        typer.echo("No symbols provided", err=True)
        raise typer.Exit(code=1)

    payload = fetch_market_metrics_raw(syms)
    items = payload.get("data", {}).get("items", [])

    by_symbol = {}
    for item in items:
        sym = (item.get("symbol") or "").upper()
        if not sym:
            continue
        by_symbol[sym] = item

    for sym in syms:
        item = by_symbol.get(sym)
        if not item:
            print(f"Symbol: {sym}")
            print("  (no item returned from /market-metrics)")
            print()
            continue

        fields = {
            "implied-volatility-index-rank": item.get("implied-volatility-index-rank"),
            "tw-implied-volatility-index-rank": item.get("tw-implied-volatility-index-rank"),
            "tos-implied-volatility-index-rank": item.get("tos-implied-volatility-index-rank"),
        }
        ivr = _extract_ivr_from_item(item)

        print(f"Symbol: {sym}")
        print(f"  raw_fields: {fields}")
        if ivr is None:
            print("  extracted_ivr: None")
        else:
            print(f"  extracted_ivr: {ivr:.6f} ({ivr*100:.2f}%)")
        print()
```

Usage (run manually, **not** in tests):

```bash
export STRATDECK_DATA_MODE=live

python -m stratdeck.cli ivr-debug SPX,AMZN,NVDA,GOOGL,AAPL
```

This lets the human user compare:

- Raw IVR-related fields from `/market-metrics`.
- The final `extracted_ivr` we store and use.

### 2.2 Optional: JSON dump mode

If desired, you can add an optional flag such as `--json` that dumps the same information as JSON. This is handy for piping into files or tools, but **not required** for this task.

---

## Task 3 — Snapshot & Loader Sanity Test

**Goal:** Ensure `build_iv_snapshot()` and `load_snapshot()` preserve IVR values correctly and return the intended 0–1 floats.

### 3.1 Add a test module for the snapshot

You can add a small test in `tests/test_iv_snapshot_roundtrip.py`:

```python
from stratdeck.tools.vol import load_snapshot

def test_load_snapshot_handles_both_formats(tmp_path):
    # Prepare two formats that load_snapshot claims to support

    path = tmp_path / "iv_snapshot.json"

    # Format A: {SYM: {"ivr": x}}
    data_a = {
        "SPX": {"ivr": 0.15},
        "AAPL": {"ivr": 0.07},
    }
    path.write_text(json.dumps(data_a))

    snapshot = load_snapshot(path=str(path))
    assert snapshot["SPX"] == pytest.approx(0.15)
    assert snapshot["AAPL"] == pytest.approx(0.07)

    # Format B: {SYM: x}
    data_b = {
        "SPX": 0.15,
        "AAPL": 0.07,
    }
    path.write_text(json.dumps(data_b))

    snapshot = load_snapshot(path=str(path))
    assert snapshot["SPX"] == pytest.approx(0.15)
    assert snapshot["AAPL"] == pytest.approx(0.07)
```

This ensures the loader logic doesn’t accidentally mangle IVR values.

### 3.2 Avoid real network calls in tests

Do **not** call `build_iv_snapshot()` directly in tests if it would perform real HTTP calls. If you want to test it, patch out the network dependency (e.g., monkeypatch `fetch_iv_rank_for_symbols` to return synthetic data). However, that is optional for this slice; the main goal is to test `_extract_ivr_from_item` and `load_snapshot`.

---

## Task 4 — Manual IVR Alignment Check (Human-in-the-loop)

**Goal:** Provide a clear sequence for the human user to compare StratDeck IVR values against the Tasty UI and decide if any further adjustment to `_extract_ivr_from_item` is needed.

This step is **manual**, not coded as tests.

### 4.1 Refresh IV snapshot

From the project root:

```bash
cd /Users/christaylor/Projects/stratdeck-copilot
source .venv/bin/activate  # adjust if needed

export STRATDECK_DATA_MODE=live

python -m stratdeck.cli refresh-ivr-snapshot
```

### 4.2 Run trade-ideas and inspect IVR in output

```bash
python -m stratdeck.cli trade-ideas   --universe index_core   --strategy short_put_spread_index_45d   --json-output /tmp/ideas_ivr_alignment_check.json
```

Inspect the IVR values:

```bash
jq '.[] | {symbol, ivr, pop, credit_per_width}'   /tmp/ideas_ivr_alignment_check.json
```

Multiply each `ivr` by 100 and compare to the Tasty watchlist IV Rank UI figures for the same time.

### 4.3 Run `ivr-debug` for the same symbols

```bash
python -m stratdeck.cli ivr-debug SPX,AMZN,NVDA,GOOGL,AAPL
```

Compare:

- `raw_fields` vs Tasty UI (IV Rank column).
- `extracted_ivr` (×100) vs Tasty UI.

Document (outside of this codebase) any consistent discrepancies you see. If SPX/AAPL are nearly perfect but AMZN/NVDA/GOOGL differ by a few points, note the size and direction of the difference.

If the discrepancies are small (e.g. 1–3 IVR points), they may be acceptable as “UI vs backend lag / rounding”. If they’re large and consistent, you may decide to:

- Adjust `_extract_ivr_from_item` to use a different field, or
- Apply a slightly different scaling rule.

Any such changes should be accompanied by updated tests in `tests/test_market_metrics_ivr_extraction.py` and, optionally, fixture-based tests (see next task).

---

## Task 5 — Optional Fixtures for Self-Correction

**Goal (optional but recommended):** Capture real `/market-metrics` responses for a handful of symbols at a known timestamp, and assert that `_extract_ivr_from_item` returns the IVR values you consider “correct” for that snapshot.

### 5.1 Capture market-metrics JSON

Add a raw market-metrics debug CLI (optional), or reuse existing tooling, to dump JSON responses for a set of symbols (SPX, AAPL, AMZN, NVDA, GOOGL). For example, if you add a command:

```python
@app.command()
def raw_market_metrics(symbols: str = typer.Argument(...)):
    from stratdeck.data.market_metrics import fetch_market_metrics_raw

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    payload = fetch_market_metrics_raw(syms)
    print(json.dumps(payload, indent=2, sort_keys=True))
```

You can then run:

```bash
python -m stratdeck.cli raw_market_metrics SPX,AMZN,NVDA,GOOGL,AAPL   > dev/market-metrics-fixture.json
```

### 5.2 Fixture-based test

In `tests/test_market_metrics_ivr_extraction.py`, optionally add a test that:

- Loads `dev/market-metrics-fixture.json` (or a copy under `tests/fixtures/`).
- Iterates items and asserts that `_extract_ivr_from_item(item)` matches expected IVR values (the ones you manually verified against the Tasty UI at capture time), within a small tolerance (e.g. ±0.01).

This gives you a **self-correcting system**: if you ever change `_extract_ivr_from_item` and drift away from your chosen reference values, tests will fail.

Note: be careful not to commit any credentials or sensitive data in these fixtures. The market-metrics response itself should not contain secrets, but double-check.

---

## Task 6 — End-to-End Check & Final Clean-Up

Before committing and pushing the branch, perform an end-to-end check and clean up any noise.

### 6.1 Run tests

From project root:

```bash
pytest -q
```

All tests (including the new ones) must pass.

### 6.2 Run a live sanity check

With `DATA_MODE=live`:

```bash
export STRATDECK_DATA_MODE=live

# Refresh IVR
python -m stratdeck.cli refresh-ivr-snapshot

# Run trade-ideas
python -m stratdeck.cli trade-ideas   --universe index_core   --strategy short_put_spread_index_45d   --json-output /tmp/ideas_ivr_alignment_check.json

# Quick IVR view
jq '.[] | {symbol, ivr, pop, credit_per_width}'   /tmp/ideas_ivr_alignment_check.json
```

Confirm:

- IVR values are reasonable (0–1 floats, not crazy values).
- Multiplying by 100 roughly matches Tasty UI IV Rank, within a tolerance you’re happy with.

### 6.3 Git hygiene

Check what changed:

```bash
git status
git diff --stat
git diff
```

Make sure only the intended files are modified:

- `stratdeck/data/market_metrics.py` (if you changed logic).
- `stratdeck/cli.py` (for the `ivr-debug` command, and optionally `raw_market_metrics`).
- `tests/test_market_metrics_ivr_extraction.py`
- `tests/test_iv_snapshot_roundtrip.py`
- Any optional fixture/test files you added.

Do **not** commit:

- `.stratdeck/last_trade_ideas.json`
- Temporary JSON outputs under `/tmp`.
- Any local-only debug scripts (unless you explicitly intend to keep them).

If `.stratdeck/last_trade_ideas.json` is dirty:

```bash
git restore .stratdeck/last_trade_ideas.json
```

### 6.4 Commit and push

Stage only intended files:

```bash
git add stratdeck/data/market_metrics.py
git add stratdeck/cli.py
git add tests/test_market_metrics_ivr_extraction.py
git add tests/test_iv_snapshot_roundtrip.py
# and any fixtures/tests you added intentionally:
# git add tests/fixtures/...
```

Commit:

```bash
git commit -m "Add IVR debug CLI and tests for market-metrics IVR extraction"
```

Push:

```bash
git push -u origin feature/ivr-alignment-recheck
```

Then open a PR from `feature/ivr-alignment-recheck` → `main` on GitHub and verify CI.

After merge:

```bash
git checkout main
git pull --ff-only origin main
git branch -d feature/ivr-alignment-recheck
# optional: git push origin --delete feature/ivr-alignment-recheck
```

---

## What Not To Change

While executing this Codex-Max task, **do not**:

- Alter the filters engine or `TradePlanner` behaviour.
- Change the shape of `iv_snapshot.json` (formats A and B are already supported; keep that compatibility).
- Introduce real HTTP calls in tests — all tests must use synthetic data or fixtures.
- Touch unrelated modules unless necessary for IVR debugging.

Focus narrowly on:

- `_extract_ivr_from_item` correctness and tests.
- A clear, usable `ivr-debug` CLI.
- Snapshot/loader sanity tests.
- Optional fixtures for long-term self-correction.
