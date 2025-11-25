# Codex-Max Task Spec – Live IV Rank from Tasty (StratDeck)

## Metadata

- **Repo**: `git@github.com:theglove44/stratdeck.git`
- **Default branch**: `main`
- **Feature branch**: `feature/live-ivr-rank`
- **Language**: Python 3.x
- **Primary test command**: `pytest -q`
- **CLI entrypoint**: `python -m stratdeck.cli ...`

## High-Level Objective

Add a robust, live-backed **Implied Volatility Rank (IVR)** pipeline powered by Tastytrade’s market-metrics API, and wire it into the existing StratDeck IV/IVR machinery.

The system must:

1. Resolve the **live universe symbol set** (indices + all Tasty watchlist universes).
2. Fetch **IV Rank** from Tasty’s market-metrics endpoint for those symbols.
3. Write a local snapshot file `stratdeck/data/iv_snapshot.json` in a consistent, well-defined format.
4. Ensure existing IVR consumers (trade planner, scan cache, CLI output, position monitor, agents, scoring) transparently see these new IVR values with **no behavioural regressions** besides improved data quality.
5. Provide a CLI command to refresh the IV snapshot.
6. Have full test coverage (no real network calls in tests).

IV Rank must be treated as a **0–1** float (0.25 = 25 IVR) inside StratDeck.

---

## Constraints and Conventions

- **No real network calls in tests.** All Tasty API interactions must be mocked/faked.
- **Do not refactor unrelated parts** of the system; keep this change focused on live IVR + snapshot.
- Preserve current **strategy filters semantics**:
  - `min_ivr` / `max_ivr` in `strategies.yaml` are 0–1 floats (e.g. `min_ivr: 0.20` means IVR ≥ 20).
- Preserve current **position-monitor semantics**:
  - Exit rules (`ivr_soft_exit_below`) are in percentage scale (0–100).
  - Internal raw IVR (0–1) is converted to % using the existing `_to_percent` helper.
- Maintain support for both snapshot shapes in `vol.load_snapshot()`:
  - `{ "SPX": 0.32 }`
  - `{ "SPX": { "ivr": 0.32 } }`
- Code must pass `pytest -q` from repo root.
- All new Python code must be typed and idiomatic, with small, well-named helpers and docstrings where relevant.

---

## Existing State (for orientation)

> Codex: **Do not change these behaviours; use them as constraints. Validate they still hold after you're done.**

1. `stratdeck/tools/vol.py`
   - Provides `load_snapshot(path: Optional[PathLike] = None) -> Dict[str, float]`.
   - Reads `stratdeck/data/iv_snapshot.json` by default.
   - Accepts both `{ "SPX": { "ivr": 0.42 } }` and `{ "SPX": 0.42 }` and always returns `{ "SPX": 0.42 }` (0–1 IVR).
   - If the file is missing, falls back to a tiny built-in mapping like `{ "SPX": 0.35, "XSP": 0.38, "QQQ": 0.29, "IWM": 0.33 }` (all 0–1).

2. `stratdeck/data/tasty_provider.py`
   - Has a `TastyProvider` class with `get_ivr(self, symbol: str) -> Optional[float]`.
   - Uses a Tasty endpoint (currently `/market-metrics/IVR` or similar) and reads `implied-volatility-index-rank` from the JSON.
   - Returns a raw float that should be **0–1** (Tasty rank field is 0–1 for standard API responses).

3. `stratdeck/tools/scan_cache.py`
   - Exposes `attach_ivr_to_scan_rows(rows, iv_snapshot, symbol_keys=None)`.
   - Docstring suggests `iv_snapshot` is shaped like `{ "SPX": {"ivr": 32.1, ...}, ... }`.
   - Implementation:
     - Picks a symbol per row (`symbol` or `data_symbol`).
     - `vol_info = iv_snapshot.get(symbol, {})` then `ivr = vol_info.get("ivr")`.
     - If present, attaches `row["ivr"] = ivr` (no scale conversion).

4. `stratdeck/tools/position_monitor.py`
   - Computes per-position metrics, including IVR, using:
     - Primary source: `provider.get_ivr(symbol)`.
     - Fallback: `vol_snapshot.get(symbol_upper)` if a snapshot is passed.
   - Raw IVR is then passed through `_to_percent`:
     - If `0 <= val <= 1` multiply by 100, else leave as-is.
   - `ivr_soft_exit_below` thresholds in `config/exits.yaml` are in the **0–100** range.

5. `stratdeck/agents/trade_planner.py`
   - Uses `ivr = row.get("ivr")` (and falls back to `iv_rank` fields if missing).
   - Strategy filters use `min_ivr` / `max_ivr` from `strategies.yaml` as 0–1 floats and reject candidates when `ivr < min_ivr`.

6. `stratdeck/cli.py`
   - For output formatting, IVR is printed as a percentage via:
     - `ivr_pct = int(ivr * 100) if ivr <= 1 else int(ivr)`.

7. `stratdeck/agents/scout.py`
   - Has an internal `IVR` mapping and uses 0–1 IVR for credit and POP estimates.
   - `_live_ivr` uses `provider.get_ivr(symbol)` then falls back to the static map if None.

**Do not break any of this.** The goal is to improve IVR data quality, not change semantics.

---

## Target Design Overview

### Snapshot Shape (On Disk)

Standardise on a nested JSON snapshot for new writes:

```jsonc
{
  "SPX": { "ivr": 0.32 },
  "XSP": { "ivr": 0.29 },
  "AAPL": { "ivr": 0.47 }
}
```

- Keys: uppercased symbols.
- Values: objects with at least an `ivr` field (0–1 float).

`vol.load_snapshot()` will continue to support both nested and flat forms, but the new builder must write **only** the nested shape.

### Internal IVR Conventions

- Internal raw IVR: 0–1 float.
- Strategy filters in `strategies.yaml` use 0–1 IVR (e.g. `min_ivr: 0.20` means IVR ≥ 20).
- For display or threshold logic that uses 0–100 values, convert from 0–1 using existing helpers (do not duplicate).

---

## Task List

> Codex: Execute these tasks in order. Only deviate if the repo reality requires it, and document deviations in comments or commit messages.

### Task 0 – Setup and Branch

1. From a clean working directory:

   ```bash
   git clone git@github.com:theglove44/stratdeck.git
   cd stratdeck
   git checkout main
   git pull
   ```

2. Create and switch to the feature branch:

   ```bash
   git checkout -b feature/live-ivr-rank
   ```

3. Verify tests currently pass:

   ```bash
   pytest -q
   ```

   - If they do not pass on `main`, stop and surface the failure in a comment at the top of the codex file. Do **not** fix unrelated failures.

---

### Task 1 – Inspect Existing IV/IVR Code

Goal: confirm current behaviour and spot any subtle expectations before you add new code.

1. Open and read:
   - `stratdeck/tools/vol.py`
   - `stratdeck/data/tasty_provider.py`
   - `stratdeck/tools/scan_cache.py`
   - `stratdeck/tools/position_monitor.py`
   - `stratdeck/agents/trade_planner.py`
   - `stratdeck/cli.py`
   - `stratdeck/agents/scout.py`
   - `stratdeck/config/strategies.yaml`
   - `stratdeck/config/exits.yaml`
2. For each, confirm:
   - Expected IVR scale (0–1 vs 0–100).
   - Whether they expect nested snapshots (`{SYM: {"ivr": ...}}`) or flat ones.
   - How they behave when IVR is missing (tolerant vs hard filter).
3. Do **not** change any of these files in this step; this is reconnaissance.

---

### Task 2 – Implement Market-Metrics Helper

Create a new module:

- `stratdeck/data/market_metrics.py`

#### 2.1 Responsibilities

- Fetch IV Rank for a batch of symbols from Tasty market-metrics.
- Normalise/clamp IVR into 0–1 floats.
- Handle both `{ "data": { "items": [...] } }` and `{ "items": [...] }` response envelopes.
- Handle chunking, missing data, malformed responses.

#### 2.2 Implementation

In `stratdeck/data/market_metrics.py`:

1. Imports:

   ```python
   from __future__ import annotations

   from typing import Any, Dict, Iterable, List, Optional

   import logging
   import requests

   from .tasty_provider import make_tasty_session_from_env, API_BASE

   logger = logging.getLogger(__name__)
   ```

2. Helper `_extract_ivr_from_item` to pull a 0–1 IVR from a market-metrics item:

   ```python
   def _extract_ivr_from_item(item: Dict[str, Any]) -> Optional[float]:
       """Extract a 0–1 IV Rank from a Tasty market-metrics item."""
       if not isinstance(item, dict):
           return None

       raw = (
           item.get("tw-implied-volatility-index-rank")
           or item.get("implied-volatility-index-rank")
       )
       if raw is None:
           return None

       try:
           ivr = float(raw)
       except (TypeError, ValueError):
           return None

       if ivr < 0:
           return None

       if ivr <= 1.5:
           norm = ivr
       elif ivr <= 150:
           norm = ivr / 100.0
       else:
           return None

       if norm < 0 or norm > 1:
           return None

       return norm
   ```

3. Helper `_items_from_response` to normalise payload shape:

   ```python
   def _items_from_response(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
       """Normalise market-metrics payloads to a list of item dicts."""
       if not isinstance(payload, dict):
           return []

       data = payload.get("data") or payload
       items = data.get("items")
       if not isinstance(items, list):
           return []

       return [i for i in items if isinstance(i, dict)]
   ```

4. Batch fetcher `fetch_iv_rank_for_symbols`:

   ```python
   DEFAULT_CHUNK_SIZE = 50


   def fetch_iv_rank_for_symbols(
       symbols: Iterable[str],
       *,
       session: Optional[requests.Session] = None,
       chunk_size: int = DEFAULT_CHUNK_SIZE,
   ) -> Dict[str, float]:
       """Fetch IV Rank for a batch of symbols via Tasty market-metrics.

       Returns {SYMBOL_UPPER: ivr_float_0_to_1}.
       """
       syms = sorted({(s or "").upper() for s in symbols if s})
       if not syms:
           return {}

       session = session or make_tasty_session_from_env()
       out: Dict[str, float] = {}

       for i in range(0, len(syms), chunk_size):
           chunk = syms[i : i + chunk_size]
           if not chunk:
               continue

           params = [("symbol", s) for s in chunk]
           try:
               resp = session.get(
                   f"{API_BASE}/market-metrics",
                   params=params,
                   timeout=30,
               )
           except Exception as exc:  # pragma: no cover
               logger.warning("Error fetching market-metrics chunk: %s", exc)
               continue

           if resp.status_code >= 400:
               logger.warning(
                   "market-metrics request failed: status=%s symbols=%s",
                   resp.status_code,
                   ",".join(chunk),
               )
               continue

           try:
               payload = resp.json()
           except ValueError:
               logger.warning(
                   "market-metrics response is not JSON for symbols=%s",
                   ",".join(chunk),
               )
               continue

           for item in _items_from_response(payload):
               sym = (item.get("symbol") or "").upper()
               if not sym:
                   continue
               ivr = _extract_ivr_from_item(item)
               if ivr is None:
                   continue
               out[sym] = ivr

       return out
   ```

5. Do not change `TastyProvider.get_ivr` in this task; it can continue to use its existing endpoint. Refactoring it to reuse `_extract_ivr_from_item` is optional and low priority.

---

### Task 3 – Implement IV Snapshot Builder

Create a focused tool to rebuild `iv_snapshot.json` from the live universe.

#### 3.1 Resolve the live universe symbol set

1. Inspect `stratdeck/data/factory.py` (or equivalent) to find how the live data factory determines which symbols to stream (indices + all Tasty watchlist universes).
2. If there is already a helper that returns this full symbol set, reuse it (e.g. `get_live_universe_symbols`).
3. If not, factor out the minimum required logic into a new public helper in the data layer:

   ```python
   def get_live_universe_symbols() -> set[str]:
       ...
   ```

   - This helper must be side-effect free and must not perform HTTP calls; it should only depend on configuration and existing resolvers.

#### 3.2 New builder module

Create `stratdeck/tools/build_iv_snapshot.py` with something like:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from stratdeck.data.market_metrics import fetch_iv_rank_for_symbols
from stratdeck.tools import vol

# Default path: stratdeck/data/iv_snapshot.json
IV_SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "iv_snapshot.json"


def resolve_live_universe_symbols() -> set[str]:
    """Return the set of symbols for which we want IV Rank."""
    from stratdeck.data.factory import get_live_universe_symbols  # adjust name as needed

    return set(get_live_universe_symbols())


def build_iv_snapshot(path: Path = IV_SNAPSHOT_PATH) -> Dict[str, Dict[str, float]]:
    """Build and write an IV snapshot JSON file with nested shape."""
    symbols = sorted(resolve_live_universe_symbols())
    if not symbols:
        snapshot: Dict[str, Dict[str, float]] = {}
    else:
        ivr_map = fetch_iv_rank_for_symbols(symbols)
        snapshot = {
            sym: {"ivr": float(ivr)} for sym, ivr in sorted(ivr_map.items())
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)

    return snapshot
```

- Use the tmp + replace pattern to avoid partial writes.
- Do not hard-code symbol lists here; rely on the live-universe resolver.

---

### Task 4 – Add CLI Command for Refreshing IV Snapshot

Add a CLI command to run the builder from the command line.

1. Open `stratdeck/cli.py` and follow the existing CLI pattern.
2. Add a new subcommand, e.g. `refresh-ivr-snapshot`:

   ```python
   # Pseudocode – adapt to actual CLI structure
   if args.command == "refresh-ivr-snapshot":
       from stratdeck.tools.build_iv_snapshot import build_iv_snapshot

       snapshot = build_iv_snapshot()
       print(f"Refreshed IV snapshot for {len(snapshot)} symbols")
       return 0
   ```

3. Ensure help text explains that it:
   - Resolves the live-universe symbol set.
   - Calls Tasty market-metrics.
   - Writes `stratdeck/data/iv_snapshot.json`.
4. This command should not run automatically in normal flows or tests; it is a manual admin utility.

---

### Task 5 – Align Snapshot Consumers (Minimal Changes Only)

1. `stratdeck/tools/vol.py`:
   - Verify `load_snapshot` still accepts both nested and flat snapshot shapes and returns `{SYM: 0–1 ivr}`.
   - Only adjust comments/docstrings if needed; avoid changing logic unless there is a bug.

2. `stratdeck/tools/scan_cache.py`:
   - Confirm `attach_ivr_to_scan_rows` works with nested snapshot `{SYM: {"ivr": 0.32}}`.
   - Ensure it attaches raw 0–1 ivr to `row["ivr"]` (no extra scaling).
   - Optionally, add a short comment clarifying expected snapshot shape and scale.

3. `stratdeck/tools/position_monitor.py`:
   - Ensure snapshot-based fallback uses `vol.load_snapshot()` (flat `{SYM: 0–1}` map) if it does not already.
   - Verify `_to_percent` is responsible for converting to 0–100 for metrics and exit rules.

4. `stratdeck/cli.py` and `stratdeck/agents/trade_planner.py`:
   - Confirm they operate on 0–1 IVR for filter logic.
   - Conversion to percentage should only happen at the display/label edge.

---

### Task 6 – Tests for Market-Metrics Helper

Add tests for the new helper module.

1. Create `tests/data/test_market_metrics_ivrank.py`.

2. Cover:

- `_extract_ivr_from_item`:
  - Prefers `tw-implied-volatility-index-rank` over `implied-volatility-index-rank` when both present.
  - Falls back to `implied-volatility-index-rank` when `tw-...` missing.
  - Scales values like `42.0` down to `0.42` when obviously 0–100.
  - Returns `None` for negative, huge, or non-numeric values.

- `_items_from_response`:
  - Handles `{"data": {"items": [...]}}` and `{"items": [...]}`.
  - Returns `[]` for malformed payloads.

- `fetch_iv_rank_for_symbols` (with a fake session):
  - Single-chunk happy path: a few symbols, partial coverage, correct result map.
  - Multi-chunk path: set `chunk_size=2` and ensure both chunks are used.
  - Handles `status_code >= 400` by skipping that chunk.
  - Handles `json()` failures by skipping that chunk.

3. All tests must use fakes/mocks; no real HTTP requests.

---

### Task 7 – Tests for Snapshot Builder

Add tests for the builder module.

1. Create `tests/tools/test_build_iv_snapshot.py`.

2. Use `tmp_path` and monkeypatching:

- Monkeypatch `resolve_live_universe_symbols` to return a small set, e.g. `{"SPX", "AAPL"}`.
- Monkeypatch `fetch_iv_rank_for_symbols` to return e.g. `{"SPX": 0.32, "AAPL": 0.45}`.

3. Tests:

- `test_build_iv_snapshot_writes_nested_structure`:
  - Call `build_iv_snapshot(tmp_path / "iv_snapshot.json")`.
  - Assert JSON on disk is nested `{SYM: {"ivr": value}}` with the ivr values from the fake map.

- `test_build_iv_snapshot_round_trip_with_load_snapshot`:
  - After writing snapshot, call `vol.load_snapshot(path)`.
  - Assert it returns a flat `{SYM: ivr}` dict with the same values.

- `test_build_iv_snapshot_handles_empty_universe`:
  - `resolve_live_universe_symbols` returns empty set.
  - Snapshot file should be `{}` and `vol.load_snapshot(path)` should return `{}`.

---

### Task 8 – Light Smoke Tests of Consumers

1. Scan cache:

- Add/extend a test in e.g. `tests/tools/test_scan_cache.py`:
  - Use rows `[{"symbol": "SPX"}]` and `iv_snapshot = {"SPX": {"ivr": 0.32}}`.
  - Call `attach_ivr_to_scan_rows` and assert the result row has `"ivr": 0.32`.

2. Position monitor (optional but good):

- If tests exist, add one that passes a simple snapshot dict from `vol.load_snapshot` and verifies IVR is converted to a 0–100 value for metrics.
- If this is too coupled for now, you may skip adding new tests here, but do not break existing ones.

---

### Task 9 – Run Full Test Suite

From repo root, run:

```bash
pytest -q
```

- If there are pre-existing unrelated failures, do not attempt to fix them; just note them in code comments as pre-existing.

---

### Task 10 – Manual Sanity Checks (Optional, Requires Credentials)

If you have valid Tasty credentials and a working environment, you can run these manual checks (optional but recommended):

1. Refresh snapshot:

   ```bash
   python -m stratdeck.cli refresh-ivr-snapshot
   ```

   - Confirm it prints the number of symbols refreshed.

2. Inspect snapshot shape:

   ```bash
   head stratdeck/data/iv_snapshot.json
   ```

3. Run a trade-ideas scan for a universe that uses Tasty watchlist symbols and confirm IVR values in the JSON output are 0–1 and not all default/fallback values.

If you cannot run these checks (no credentials), skip this task.

---

### Task 11 – Commit and Push

1. Check git status:

   ```bash
   git status
   ```

   - Ensure only intended files are changed (new market-metrics module, builder, CLI wiring, tests, minimal doc tweaks).

2. Stage changes (adjust filenames as needed):

   ```bash
   git add stratdeck/data/market_metrics.py \
          stratdeck/tools/build_iv_snapshot.py \
          stratdeck/cli.py \
          stratdeck/tools/vol.py \
          tests/data/test_market_metrics_ivrank.py \
          tests/tools/test_build_iv_snapshot.py \
          tests/tools/test_scan_cache.py
   ```

3. Commit:

   ```bash
   git commit -m "Add Tasty market-metrics IV Rank source and refresh iv_snapshot pipeline"
   ```

4. Push the feature branch:

   ```bash
   git push -u origin feature/live-ivr-rank
   ```

---

### Task 12 – PR Notes (for Human Reviewers)

When opening the PR, include a summary along the lines of:

- **What changed**
  - New `stratdeck/data/market_metrics.py` to fetch 0–1 IVR values from Tasty market-metrics for batches of symbols.
  - New `stratdeck/tools/build_iv_snapshot.py` to rebuild `stratdeck/data/iv_snapshot.json` using the live-universe symbol set.
  - New CLI subcommand `refresh-ivr-snapshot`.
  - Tests for market-metrics parsing, IVR extraction, snapshot building, and scan cache integration.

- **Why**
  - Replace brittle hard-coded IVR and half-empty snapshots with a robust, live-backed IVR pipeline covering all live universes (indices + Tasty watchlists).

- **How to exercise**
  - `python -m stratdeck.cli refresh-ivr-snapshot`
  - `python -m stratdeck.cli trade-ideas --universe ... --strategy ... --json-output`

- **Risks / considerations**
  - Snapshot refresh depends on Tasty market-metrics and valid credentials.
  - Tests fully mock out HTTP; no change to test runtime assumptions.

This completes the end-to-end Codex-Max spec for integrating live IVR Rank from Tasty into StratDeck.