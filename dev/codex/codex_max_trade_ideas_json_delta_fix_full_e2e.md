# Codex-Max Task Spec — Fix `trade-ideas --json-output` to Emit Leg Deltas (Full E2E + Retest Loop)

## 0. Meta

- **Repo**: `git@github.com:theglove44/stratdeck.git`
- **Local path**: `~/Projects/stratdeck-copilot`
- **Branch**: `feature/human-strategy-engine` (use current working branch)
- **Primary command under test**:
  ```bash
  export STRATDECK_DATA_MODE=live

  python -m stratdeck.cli trade-ideas     --universe tasty_watchlist_chris_historical_trades     --strategy short_put_spread_equity_45d     --max-per-symbol 1     --json-output
  ```

- **Current behaviour**:
  - Human-readable CLI output shows correct leg deltas (e.g. `0.30`, `0.25`) for equity short put spreads (AMD, GOOGL, etc).
  - The JSON output from `--json-output` still shows:
    ```json
    "short_legs": [
      { "delta": null, ... }
    ]
    ```

- **Goal**:
  - Make `trade-ideas --json-output` emit full `TradeIdea` / `TradeLeg` JSON including populated `delta` and `dte` fields, consistent with what the models currently contain and with other CLI commands that already use `model_dump(mode="json")`.
  - Include a full end-to-end test/fix/retest loop so Codex re-runs pytest and CLI checks after any change until outputs match expectations.

---

## 1. Discovery

Codex must:

1. Open `stratdeck/cli.py`.
2. Locate the `trade-ideas` CLI command:
   - It has a `--json-output` option defined around line ~1055.
   - It later builds a `payload` and does:
     ```python
     if json_output:
         blob = json.dumps(payload, indent=2, default=str)
         click.echo(blob)
     ```
3. Confirm that for `trade-ideas` specifically:
   - `payload` is currently **hand-built** (manual dicts), not `idea.model_dump(mode="json")`.
   - Other commands in the same file already follow the correct pattern. For example, `positions_list` does:
     ```python
     payload = [p.model_dump(mode="json") for p in pos_list] if pos_list else []
     click.echo(json.dumps(payload, indent=2, default=str))
     ```
4. Note in a short code comment where `trade-ideas` diverges from this pattern (for future reference).

---

## 2. Implementation Requirements

### 2.1 Replace manual payload with model_dump

Codex must:

1. Replace any manual `payload` construction for `trade-ideas` with a direct dump from the Pydantic models. The canonical version should be:

   ```python
   payload = [idea.model_dump(mode="json") for idea in ideas]
   ```

   Requirements:

   - Prefer `model_dump(mode="json")` if available on `idea`.
   - If `model_dump` is not available (edge case, older Pydantic), detect that and **only then** fall back to `.dict()` with equivalent behaviour.
   - Ensure nested legs are included and not pruned:
     - Each leg must carry `kind` / `type`, `side`, `strike`, `expiry`/`exp`, `quantity`, `delta`, `dte`, and any other fields already defined on `TradeLeg`.

2. Ensure JSON is serialized once and reused:

   ```python
   blob = json.dumps(payload, indent=2, default=str)

   if output_path:
       output_path.write_text(blob, encoding="utf-8")
       click.echo(f"Wrote {len(payload)} ideas to {output_path}")

   if json_output:
       click.echo(blob)
   ```

3. Keep the existing guard for `--output-path`:

   ```python
   if output_path and not json_output:
       raise click.ClickException("--output-path requires --json-output.")
   ```

4. Preserve CLI semantics:
   - Human-readable output path remains unchanged when `--json-output` is **not** passed.
   - With `--json-output` only: JSON is printed to stdout.
   - With both `--json-output` and `--output-path`: JSON is written to file and also echoed (or left consistent with current behaviour, but must not regress).

### 2.2 Preserve or extend structure safely

Codex must:

- Keep all existing top-level keys in the `TradeIdea` JSON that other tools may depend on (e.g. `symbol`, `dte`, `spread_width`, `strategy_id` / `strategy_type`, `direction`, `short_legs`, `long_legs`, `ivr`, `pop`, `credit_per_width`).
- Additional fields that appear when using `model_dump(mode="json")` are allowed (and welcomed) as long as:
  - Existing keys still exist with compatible types.
  - The payload is fully JSON-serializable.

---

## 3. End-to-End Tests & Checks (Before and After Fix)

Codex must adopt the following **E2E sequence** and re-run it after any change until all checks pass.

### 3.1 Baseline: run tests (before changes)

From repo root:

```bash
cd ~/Projects/stratdeck-copilot
pytest -q
```

- Confirm initial state is green.
- If not green *before* changes, Codex must record which tests are failing but should not attempt to fix unrelated failures unless they block this task.

### 3.2 E2E Check 1 — JSON CLI for equity strategy (mock mode)

Run this in **mock** mode to avoid live-data fragility:

```bash
export STRATDECK_DATA_MODE=mock

python -m stratdeck.cli trade-ideas   --universe tasty_watchlist_chris_historical_trades   --strategy short_put_spread_equity_45d   --max-per-symbol 1   --json-output > /tmp/ideas_equity_put_mock.json

jq '.[] | {symbol, dte, width: .spread_width, short_delta: .short_legs[0].delta}'   /tmp/ideas_equity_put_mock.json
```

Expected after the fix:

- `dte` is a non-null integer (mocked 45D behaviour is acceptable).
- `width` matches the configured rules (`by_price_bracket` from `strategies.yaml`).
- `short_delta` is **non-null** and matches the leg delta seen in CLI debug output for the same run.

Codex must capture and inspect this output to verify that `short_delta` is no longer `null`.

### 3.3 E2E Check 2 — JSON CLI for equity strategy (live mode, if available)

If Codex’ environment can access live Tasty data, run:

```bash
export STRATDECK_DATA_MODE=live

python -m stratdeck.cli trade-ideas   --universe tasty_watchlist_chris_historical_trades   --strategy short_put_spread_equity_45d   --max-per-symbol 1   --json-output > /tmp/ideas_equity_put_live.json

jq '.[] | {symbol, dte, width: .spread_width, short_delta: .short_legs[0].delta}'   /tmp/ideas_equity_put_live.json
```

Expected:

- `dte` ~ 45 (within expected monthly window).
- `width` matches the `by_price_bracket` rule (e.g., 5-wide for higher-priced underlyings).
- `short_delta` is **non-null**, consistent with the previously observed CLI debug values (roughly 0.25–0.35 for short puts).

If live mode is **not** available in Codex’ environment, Codex must explicitly note this and only enforce the mock-mode check as the E2E verification.

### 3.4 E2E Check 3 — `--output-path` behaviour

Run:

```bash
export STRATDECK_DATA_MODE=mock

python -m stratdeck.cli trade-ideas   --universe tasty_watchlist_chris_historical_trades   --strategy short_put_spread_equity_45d   --max-per-symbol 1   --json-output   --output-path /tmp/ideas_equity_put_mock.json
```

Codex must verify (by inspection):
- `/tmp/ideas_equity_put_mock.json` exists.
- The file content is valid JSON.
- The first idea’s `short_legs[0].delta` is **non-null**.

### 3.5 E2E Check 4 — Human-readable path unchanged

Run (no JSON flag):

```bash
export STRATDECK_DATA_MODE=mock

python -m stratdeck.cli trade-ideas   --universe tasty_watchlist_chris_historical_trades   --strategy short_put_spread_equity_45d   --max-per-symbol 1
```

Codex must ensure:

- Human-readable output still shows:
  - Correct rationale lines.
  - Correct legs (short/long, strikes, expiries).
- No new debug lines are left in (e.g. `[DEBUG] leg delta`) — Codex must remove any debug statements it or the human added during earlier diagnostics.

---

## 4. Self-Check & Self-Correction Loop

Codex must implement a **strict loop** for this task:

1. **Apply minimal code changes** to switch `trade-ideas` JSON to use `model_dump(mode="json")` (or `.dict()` fallback if needed).

2. **Run unit tests**:

   ```bash
   pytest -q
   ```

   - If any tests fail:
     - Read the assertion message and stack trace.
     - Fix only what is necessary to restore previous behaviour or align tests with the new JSON shape.
     - Re-run `pytest -q` until green or until Codex hits an external limit.

3. **Run the E2E mock-mode CLI check** from §3.2.

   - If `short_delta` is `null` or JSON shape is incorrect:
     - Re-open `stratdeck/cli.py`.
     - Confirm `payload` uses `idea.model_dump(mode="json")` and is not overwritten later.
     - Confirm `TradeIdea` / `TradeLeg` models do not exclude `delta` or `dte` in their config.
     - Apply targeted fixes.
     - Go back to step 2.

4. **Run the E2E live-mode CLI check** (§3.3) if available.

   - If live data is not available or fails due to environment (e.g. network/DNS/429 issues), Codex must:
     - Document this as an environmental constraint.
     - Still proceed as long as mock-mode passes.

5. **Run the output-path check** (§3.4) and human-readable path check (§3.5).

6. Repeat steps 2–5 after each code adjustment until **all** the following hold:
   - `pytest -q` is green.
   - Mock-mode JSON shows non-null `short_delta` values.
   - (If possible) live-mode JSON shows non-null `short_delta` values.
   - `--output-path` writes valid JSON with non-null deltas.
   - Human-readable CLI output is unchanged (no debug noise, same structure).

Codex must not leave the branch in a failing state or with obviously broken CLI behaviour.

---

## 5. Done Criteria

Codex can consider this task complete only when:

1. `pytest -q` passes without errors.
2. The mock-mode command:

   ```bash
   export STRATDECK_DATA_MODE=mock

   python -m stratdeck.cli trade-ideas      --universe tasty_watchlist_chris_historical_trades      --strategy short_put_spread_equity_45d      --max-per-symbol 1      --json-output > /tmp/ideas_equity_put_mock.json

   jq '.[] | {symbol, dte, width: .spread_width, short_delta: .short_legs[0].delta}'      /tmp/ideas_equity_put_mock.json
   ```

   shows `short_delta` populated (non-null) for at least one idea.

3. If live data is available, the analogous live-mode command produces non-null `short_delta` values.

4. The `--output-path` option writes valid JSON with non-null deltas and retains the same top-level structure as stdout JSON.

5. The human-readable `trade-ideas` CLI output remains stable, with no leftover debug logging and with leg lists/expiry/width unchanged.

6. Codex has **not** regressed any other JSON-emitting CLI command in `stratdeck/cli.py` (e.g., `positions_list`, `positions_monitor`, `positions_*`, `chartist`, `scan_ta`, `enter_auto`, `ideas_vet`), especially where they already use `model_dump(mode="json")`.

Codex should leave a very short comment in `trade-ideas`’ JSON path noting that `model_dump(mode="json")` is intentionally used to keep JSON outputs aligned with the Pydantic models and to avoid missing fields like `delta`.
