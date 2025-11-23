# PROJECT: StratDeck Agent System – Phase 3 DTE & Snapshot Hygiene Fixes

## Background

After merging Phase 3 (position monitoring and exit rules) and the widthless credit fix, an end-to-end CLI test revealed that most of the core behaviour is correct, but there are several hygiene and wiring issues:

1. **IVR missing in `.stratdeck/last_trade_ideas.json`**

   - `trade-ideas --json-output` shows `ivr` populated (e.g. `0.32`) for each idea.
   - The snapshot file `.stratdeck/last_trade_ideas.json` shows the same idea with `ivr: null`.
   - The design intent is that the **snapshot mirrors the CLI JSON** (like `.stratdeck/last_position_monitoring.json`), so this mismatch is a bug.

2. **DTE is `null` for real entries by default**

   - Fresh positions created via `enter-auto` and then inspected via:

     ```bash
     python -m stratdeck.cli positions monitor --json-output
     ```

     show:

     ```json
     "dte": null,
     "action": "hold",
     "reason": "IVR_BELOW_SOFT_EXIT"   # or TARGET_PROFIT_HIT
     ```

   - This is before any manual edits to `.stratdeck/positions.json`.
   - That means the live entry path (`enter-auto`) is not storing a real option expiry on `PaperPosition`, so `compute_position_metrics` cannot compute DTE.

   > Note: In a later step of the manual test, `expiry` was intentionally edited to a past date and DTE became `0.0`, which correctly triggered `DTE_BELOW_THRESHOLD`. That part is expected. The underlying issue is that, by default, **no expiry is present**, so DTE stays `null`.

3. **`positions list --json-output` is not guaranteed to be clean JSON**

   - Early in the test, `positions list --json-output | jq ...` worked fine.
   - After using `positions close-auto`, the same pattern produced:

     ```text
     jq: parse error: Invalid numeric literal at line 1, column 3
     ```

   - This strongly suggests that **non-JSON output (log line, warning, etc.) is being written to stdout** together with the JSON when `--json-output` is set.

We want to clean these up so that:

- Snapshots are faithful mirrors of CLI JSON.
- DTE-based rules have real data (and never fire on nonsense).
- `--json-output` flags always guarantee valid JSON on stdout.

---

## Goals

1. **Make `.stratdeck/last_trade_ideas.json` mirror `trade-ideas --json-output`**
   - IVR and other fields present in CLI JSON must be present in the snapshot.
   - There should be a consistent single source-of-truth data structure for both CLI and file output.

2. **Wire real expiry/DTE into live `PaperPosition`s**
   - Positions created by `enter-auto` should carry a proper `expiry` datetime derived from the option legs.
   - `compute_position_metrics` should compute non-null DTE for those positions.
   - DTE-based rules (`DTE_BELOW_THRESHOLD`) must only fire when DTE is valid.

3. **Guarantee that `positions list --json-output` (and similar) produces pure JSON**
   - When `--json-output` is used, stdout must contain valid JSON **only**.
   - All logs, debug messages, and banner text must go to stderr or be disabled in that mode.

4. **Add regression tests** to lock all of this in without breaking existing behaviour.

---

## Scope / Target Files

Codex should focus on:

- `stratdeck/cli.py`
  - `trade-ideas` command: how it prints JSON and writes `.stratdeck/last_trade_ideas.json`.
  - `positions` commands: `list`, `monitor`, `close-auto`, `close --id` JSON output paths.

- `stratdeck/tools/positions.py`
  - `PaperPosition` model: ensure expiry support is present and persisted.
  - Any helper that constructs `PaperPosition` for `enter-auto`.

- `stratdeck/agents/trade_planner.py` or wherever `enter-auto` lives
  - The code path that:
    - selects legs from trade ideas,
    - prices the spread using Tastytrade chains,
    - constructs and saves `PaperPosition`.

- `stratdeck/tools/position_monitor.py`
  - `compute_position_metrics(...)`: DTE calculation, IVR usage, etc.

- `tests/`
  - New or extended tests to cover:
    - trade-ideas snapshot mirroring,
    - DTE wiring for real entries,
    - JSON purity for `positions list --json-output`.

---

## Implementation Details

### 1. Make `last_trade_ideas.json` mirror CLI JSON (IVR fix)

**Current behaviour (inferred):**

- `trade-ideas --json-output` uses some in-memory representation (e.g. Pydantic models converted to dict) and prints them to stdout.
- Snapshot writing for `.stratdeck/last_trade_ideas.json` likely:
  - Either serialises from a different model without `ivr`,
  - Or uses a conversion that drops IVR (e.g. missing field, `exclude_none=True` behaviour, or a different schema).

**Desired behaviour:**

- The snapshot must be written from the **same data structure** that’s used for the CLI JSON output.
- Whatever appears in `--json-output` for index `0` must appear identically in `.stratdeck/last_trade_ideas.json[0]`, at least for core fields:
  - `symbol`, `strategy`, `spread_width`, `dte_target`, `ivr`, `underlying_price_hint`, `filters_*`, etc.

**Changes:**

1. Locate the function that:
   - Builds the list of ideas used by `trade-ideas --json-output`.
   - Writes `.stratdeck/last_trade_ideas.json`.

2. Refactor so that:

   - A single list of `dict` (or Pydantic models converted to `dict`) is created once and:
     - Passed to the CLI output path (for `--json-output`).
     - Written **as-is** to `.stratdeck/last_trade_ideas.json`.

3. Remove any separate recomputation or alternative serialisation paths for the snapshot (especially those that might drop IVR).

4. Add/extend a test, e.g. `tests/test_trade_ideas_snapshot.py`:

   - Arrange:
     - Programmatically execute the same logic used by `trade-ideas --json-output` to get `ideas_list`.
     - Write the snapshot using the production path.
   - Act:
     - Load `.stratdeck/last_trade_ideas.json`.
   - Assert, for index `0`:
     - `ideas_list[0]['symbol'] == snapshot[0]['symbol']`
     - `ideas_list[0]['strategy'] == snapshot[0]['strategy']`
     - `ideas_list[0]['spread_width'] == snapshot[0]['spread_width']`
     - `ideas_list[0]['dte_target'] == snapshot[0]['dte_target']`
     - `ideas_list[0]['ivr'] == snapshot[0]['ivr']`
   - Tests should ignore ephemeral fields like timestamps or non-deterministic scores if present.

---

### 2. Wire real expiry into `PaperPosition` and compute DTE

**Current behaviour (from live test):**

- `PaperPosition` instances created via `enter-auto` end up with `metrics.dte = null` when monitored.
- Unit tests pass, which suggests:
  - Tests build `PaperPosition` fixtures with an explicit expiry or DTE.
  - Live `enter-auto` path never sets expiry, so metrics can’t compute DTE.

**Design we want:**

- `PaperPosition` persists a proper `expiry` datetime.
- For spread-based entries created via `enter-auto`, expiry should come from the actual options chain/legs selected.
  - All legs of a typical spread share the same expiration date; pick that date.
- `compute_position_metrics` computes DTE as the number of days from now to expiry.

**Changes:**

1. **Ensure `PaperPosition` has an `expiry` field** (if not already present):

   In `stratdeck/tools/positions.py`:

   ```python
   from datetime import datetime
   from pydantic import BaseModel

   class PaperPosition(BaseModel):
       # existing fields ...
       opened_at: datetime
       status: str = "open"

       # NEW (if not already):
       expiry: datetime | None = None
   ```

   - Make it optional with default `None` to preserve compatibility with existing JSON.

2. **In `enter-auto`, set `expiry` for new positions:**

   In the module that implements `enter-auto` (likely `stratdeck/agents/trade_planner.py` or similar):

   - After selecting the specific legs (options) for the spread and before instantiating `PaperPosition`:
     - Extract the common expiration date from the option chain / legs.
       - For example, if legs are built from Tastytrade chain data, each leg should carry an `expiration` or similar.
     - Convert to a `datetime` (UTC or consistent timezone) and set:

       ```python
       expiry = chosen_legs[0].expiry  # or from whichever structure holds this
       ```

   - When building `PaperPosition`:

     ```python
     paper_position = PaperPosition(
         # existing kwargs ...
         expiry=expiry,
     )
     ```

3. **Refine `compute_position_metrics` to use `position.expiry`:**

   In `stratdeck/tools/position_monitor.py`:

   - Replace any placeholder logic for DTE with:

     ```python
     from datetime import datetime, timezone

     def compute_position_metrics(position: PaperPosition, now: datetime, ...):
         # ...
         expiry = position.expiry
         if expiry is not None:
             # Ensure both datetime objects are timezone-aware or naive consistently
             if expiry.tzinfo is None:
                 expiry_dt = expiry.replace(tzinfo=timezone.utc)
             else:
                 expiry_dt = expiry

             if now.tzinfo is None:
                 now_dt = now.replace(tzinfo=timezone.utc)
             else:
                 now_dt = now

             dte = (expiry_dt - now_dt).total_seconds() / 86400.0
         else:
             dte = None
         # ...
     ```

   - Make sure `PositionMetrics.dte` gets this value and is not silently defaulted to `0.0`.

4. **Guard DTE rule evaluation when DTE is `None`:**

   In `evaluate_exit_rules(...)`:

   - Ensure the DTE rule only fires when `metrics.dte` is not `None`:

     ```python
     if metrics.dte is not None and metrics.dte <= rules.dte_exit:
         if action != "exit":
             action = "exit"
             reason = "DTE_BELOW_THRESHOLD"
         triggered_rules.append(
             f"DTE {metrics.dte:.1f} <= {rules.dte_exit} days – mechanical DTE exit"
         )
     ```

   - This prevents DTE-based exits from ever triggering on missing data.

5. **Tests:**

   Add or extend tests:

   - `tests/test_position_metrics_dte_live.py` (or integrate into `test_position_metrics.py`):

     - Build a realistic `PaperPosition` fixture with:
       - `opened_at` = some datetime,
       - `expiry` = `opened_at + 45 days`.
     - Call `compute_position_metrics` with `now = opened_at` plus a small delta.
     - Assert:
       - `metrics.dte` is close to 45 (within a small tolerance).
       - For `now` set just before expiry, DTE is just above 0.
       - For `now` set just after expiry, DTE negative (or slightly below 0).

   - `tests/test_exit_rules_dte_guard.py`:

     - Case 1: `dte=None`, `dte_exit=21`:
       - Ensure `evaluate_exit_rules` does **not** set action to `"exit"` for DTE reasons.
     - Case 2: `dte=20.0`, `dte_exit=21`:
       - Ensure `action="exit"` and `reason="DTE_BELOW_THRESHOLD"`.

   - Consider adding a higher-level test that:
     - Constructs a `PaperPosition` via the same builder the `enter-auto` path uses.
     - Verifies that `expiry` is set and that a subsequent `compute_position_metrics` call yields non-null DTE.

---

### 3. Ensure `positions list --json-output` is pure JSON

**Current symptom:**

- Early in the test, `positions list --json-output | jq ...` works.
- After running `close-auto`, the same pattern yields a `jq` parse error.
- This indicates that:
  - At least in some cases, `positions list --json-output` is printing non-JSON data (e.g. debug logs, banner, error message) to stdout alongside JSON.

**Desired behaviour:**

- When `--json-output` is passed to `positions` commands, stdout must be **only** the JSON representation of positions.
- Any logs/debug info must go either to:
  - stderr, or
  - logging configured not to emit in JSON mode.

**Changes:**

1. Inspect `positions list` implementation in `stratdeck/cli.py`:

   - Look for:
     - Additional `print()` calls before or after `json.dumps(...)`.
     - Logging statements that might default to stdout.
   - Ensure pattern is:

     ```python
     if json_output:
         click.echo(json.dumps(data, indent=2))
     else:
         # human-readable table
     ```

   - And that **no other `print`/`echo`** is used in JSON mode on stdout.

2. Logging hygiene:

   - If the app uses Python `logging`, ensure the default handler is set to stderr (or at least not to stdout) for CLI tools.
   - If there are debug prints, route them explicitly to stderr in JSON mode, e.g.:

     ```python
     import sys
     print("debug message", file=sys.stderr)
     ```

3. Tests:

   Add a test file such as `tests/test_positions_list_json_output.py`:

   - Arrange:
     - Create a small temporary `.stratdeck/positions.json` with 1–2 positions.
   - Act:
     - Use a CLI runner (e.g. `click.testing.CliRunner`) to invoke:

       ```python
       result = runner.invoke(cli, ["positions", "list", "--json-output"])
       ```

   - Assert:

     - `result.exit_code == 0`
     - `json.loads(result.output)` does **not** raise.
     - Parsed JSON is a list and has expected fields for positions.

   - This test will catch any contamination of stdout in JSON mode.

4. Optional extension:

   - Apply the same pattern to other JSON-producing commands:
     - `trade-ideas --json-output`
     - `positions monitor --json-output`
     - `positions close-auto --json-output`
     - `positions close --id ... --json-output`

   - It’s acceptable to start by enforcing strict purity for `positions list --json-output` and then extend coverage later.

---

## Non-Goals / Things NOT to Change

- Do **not** change:
  - Strategy definitions or filters from Phase 1 (universes, strategies, IVR thresholds).
  - Existing Phase 3 exit rules semantics:
    - 50% credit exits for credit spreads.
    - DTE 21-floor behaviour where DTE is valid.
    - IVR soft exits (IVR<20 tagging but not hard close).
- Do not change:
  - Data models or fields used by Phase 1 and Phase 2 in ways that break JSON compatibility.
    - All new fields must be optional or defaulted (e.g. `expiry: datetime | None = None`).

---

## Acceptance Criteria

1. **trade-ideas snapshot parity**

   - Given a `trade-ideas` invocation:

     ```bash
     python -m stratdeck.cli trade-ideas        --universe index_core        --strategy short_put_spread_index_45d        --json-output > /tmp/ideas.json
     ```

   - And reading `.stratdeck/last_trade_ideas.json`:

     ```bash
     cat /tmp/ideas.json | jq '.[0] | {symbol, strategy, spread_width, dte_target, ivr}'
     jq '.[0] | {symbol, strategy, spread_width, dte_target, ivr}'        .stratdeck/last_trade_ideas.json
     ```

   - Both commands should output the **same values** for:
     - `symbol`, `strategy`, `spread_width`, `dte_target`, `ivr`.

2. **DTE wired for live positions**

   - After creating a new position via `enter-auto`:

     ```bash
     python -m stratdeck.cli trade-ideas ...
     python -m stratdeck.cli enter-auto --qty 1 --confirm
     python -m stratdeck.cli positions monitor --json-output        | jq '.[0].metrics.dte'
     ```

   - `metrics.dte` for that position must be:
     - Non-null.
     - Positive and **greater than** the configured `dte_exit` (e.g. > 21) for a fresh 45 DTE-style trade.

3. **DTE rule only fires with valid DTE**

   - For a position where `expiry` is missing (if such a position exists in legacy data):
     - `metrics.dte` should be `null`, and
     - `evaluate_exit_rules` must not produce `reason: "DTE_BELOW_THRESHOLD"`.

4. **JSON purity for `positions list --json-output`**

   - Running:

     ```bash
     python -m stratdeck.cli positions list --json-output
     ```

     should:

     - Exit with code 0.
     - Produce stdout that `json.loads` can parse without errors.
     - Contain only JSON; any logs must go to stderr.

5. **All existing tests remain green**

   - `pytest -q` must pass:
     - All old tests from Phases 1–3.
     - All new tests added in this spec.

Once these items are implemented and all tests pass, the DTE wiring, IVR snapshot mirroring, and JSON hygiene issues from the end-to-end test will be resolved without disrupting the existing core behaviour.
