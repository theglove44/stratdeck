# Codex-Max Task Spec — Sync `short_legs` / `long_legs` Deltas with `legs`

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
  - Human-readable CLI output shows correct leg deltas on `idea.legs` (e.g. `0.30`, `0.25`).
  - JSON output from `--json-output` now uses `TradeIdea.model_dump(mode="json")` and includes deltas on `legs[i].delta`.
  - However, JSON still shows `short_legs[0].delta == null`:
    ```json
    {
      "symbol": "AMD",
      "dte": 44,
      "spread_width": 5.0,
      "short_legs": [{ "delta": null, ... }],
      "legs": [{ "delta": 0.30, ... }]
    }
    ```

- **Goal**:
  - Ensure `short_legs` and `long_legs` reuse the same `TradeLeg` objects as `legs`, so their `delta` (and `dte`) fields are populated and consistent across all three lists.
  - Add E2E tests/checks and a self-correction loop so Codex re-runs pytest and CLI checks until outputs match expectations.

---

## 1. Discovery

Codex must:

1. Locate the `TradeIdea` model:
   - Search for `class TradeIdea` in `stratdeck` (likely in `stratdeck/agents/models.py` or similar).
   - Confirm it has fields roughly like:
     - `legs: list[TradeLeg]`
     - `short_legs: list[TradeLeg]`
     - `long_legs: list[TradeLeg]`

2. Locate the `TradeLeg` model:
   - Confirm it has fields for `kind`/`type`, `side` or `quantity`, `strike`, `expiry`/`exp`, `dte`, `delta`, etc., and that `delta` is not excluded from JSON.

3. Find where `TradeIdea` instances are constructed for option strategies:
   - In `stratdeck/agents/trade_planner.py`, search for `TradeIdea(`:
     ```bash
     rg "TradeIdea\(" stratdeck/agents/trade_planner.py -n
     ```
   - Identify the code path that builds ideas for **equity short put spreads** (product_type: `equity`, option_type: `put`, width_rule: `by_price_bracket`), which corresponds to `short_put_spread_equity_45d`.

4. Understand the current `legs` / `short_legs` / `long_legs` wiring:
   - Determine whether `short_legs` and `long_legs` are:
     - Constructed as new `TradeLeg(...)` instances (duplicating data), or
     - Filtered views on a canonical `legs` list.
   - Confirm that `delta` is being set at the builder/adapter level for **some** legs (we know `idea.legs[*].delta` is non-null from previous debug output).

Codex should add brief comments in the code (where appropriate) clarifying the intended relationship between `legs`, `short_legs`, and `long_legs` (i.e., canonical vs derived views).

---

## 2. Implementation Requirements

### 2.1 Make `legs` canonical, and `short_legs`/`long_legs` derived views

Codex must refactor the relevant `TradeIdea` construction so that:

1. There is a single canonical list of `TradeLeg` instances (e.g., `all_legs` or `legs`), built by the chain/strategy builder and already carrying `delta` and `dte`.

2. `short_legs` and `long_legs` are derived from that canonical list by filtering, **without** constructing new `TradeLeg` objects.

   A typical pattern should look like:

   ```python
   # all_legs already come from the adapter/builder with delta/dte set
   all_legs: list[TradeLeg] = spread_legs  # or similar

   short_legs = [leg for leg in all_legs if getattr(leg, "quantity", 0) < 0]
   long_legs = [leg for leg in all_legs if getattr(leg, "quantity", 0) > 0]

   idea = TradeIdea(
       symbol=symbol,
       dte=dte,
       spread_width=width,
       legs=all_legs,
       short_legs=short_legs,
       long_legs=long_legs,
       # ... other fields unchanged
   )
   ```

3. If there are multiple strategy types (verticals, iron condors, etc.), Codex must ensure this pattern is used consistently wherever `TradeIdea` is instantiated, **at least** for the option strategies that use `legs` / `short_legs` / `long_legs`.

4. Codex must **not** remove `legs`, `short_legs`, or `long_legs` fields from the model; they are all still required by downstream tools.

### 2.2 Ensure deltas are set before deriving views

Codex must verify that, in the builder / adapter code responsible for constructing the legs used by the planner:

- `delta` is set from the live or mock option chain row, e.g.:

  ```python
  delta = row.get("delta")
  if delta is None and "greeks" in row:
      delta = row["greeks"].get("delta")

  leg = TradeLeg(
      kind=kind,
      strike=row["strike"],
      expiry=expiry,
      quantity=qty,
      delta=delta,
      dte=compute_dte(expiry, as_of=as_of_date),
      # ... other fields
  )
  ```

Codex should not re-implement delta fetching here; it just needs to **ensure** that the canonical legs list used for `TradeIdea.legs` already carries `delta` before deriving `short_legs` / `long_legs` from it.

---

## 3. Tests & Checks (E2E Before and After Fix)

Codex must use the following E2E sequence and repeat it after changes until all checks pass.

### 3.1 Baseline tests

From repo root:

```bash
cd ~/Projects/stratdeck-copilot
pytest -q
```

- If this initially fails, Codex must note failures but only fix issues that block this task.
- After implementing changes, this command must pass.

### 3.2 Add/extend unit tests for leg consistency

Codex must extend `tests/test_trade_idea_output_fields.py` (or create a small new test file if more appropriate) with at least one test that verifies:

1. When a `TradeIdea` is created (using mock data), `legs`, `short_legs`, and `long_legs` are consistent:

   - Build at least one idea via the planner using mock mode, for example:

     ```python
     from stratdeck.data.factory import get_provider
     from stratdeck.agents import trade_planner

     def test_short_legs_deltas_match_legs():
         # Setup mock provider / data mode if needed
         ideas = trade_planner.build_trade_ideas_for_universe_and_strategy(
             universe_id="tasty_watchlist_chris_historical_trades",
             strategy_id="short_put_spread_equity_45d",
             max_per_symbol=1,
         )
         assert ideas, "Expected at least one TradeIdea"

         idea = ideas[0]
         assert idea.legs, "legs should not be empty"
         assert idea.short_legs, "short_legs should not be empty"

         short_leg = idea.short_legs[0]
         matching_leg = next(l for l in idea.legs if l.strike == short_leg.strike and l.quantity == short_leg.quantity)

         assert matching_leg.delta is not None
         assert short_leg.delta == matching_leg.delta
     ```

   - If this exact helper doesn’t exist, Codex must adapt to whatever planner helper is already in the codebase for building trade ideas under tests.

2. The test should be robust to mock data and not depend on specific tickers; it just needs to verify the invariants:

   - Any short leg present in `short_legs` appears in `legs` with the same `strike`/`quantity`.
   - Their `delta` values are equal and **not null** (where deltas are available).

Codex must run:

```bash
pytest -q
```

and ensure all tests (including the new one) pass.

### 3.3 E2E Check — JSON CLI (mock mode)

Run:

```bash
export STRATDECK_DATA_MODE=mock

python -m stratdeck.cli trade-ideas   --universe tasty_watchlist_chris_historical_trades   --strategy short_put_spread_equity_45d   --max-per-symbol 1   --json-output > /tmp/ideas_equity_put_mock.json

jq '.[] | {symbol, dte, width: .spread_width, short_delta: .short_legs[0].delta, legs_delta: .legs[0].delta}'   /tmp/ideas_equity_put_mock.json
```

Expected after fix:

- `short_delta` is **non-null**.
- `legs_delta` is **non-null**.
- `short_delta == legs_delta` (for the corresponding short leg).

Codex must inspect this and ensure it matches expectations. If needed, Codex can adjust the `jq` expression to align the correct indices/legs (e.g. ensuring index 0 is the short leg by checking `quantity < 0`).

### 3.4 E2E Check — JSON CLI (live mode, if available)

If Codex’ environment can access live Tasty data, run:

```bash
export STRATDECK_DATA_MODE=live

python -m stratdeck.cli trade-ideas   --universe tasty_watchlist_chris_historical_trades   --strategy short_put_spread_equity_45d   --max-per-symbol 1   --json-output > /tmp/ideas_equity_put_live.json

jq '.[] | {symbol, dte, width: .spread_width, short_delta: .short_legs[0].delta, legs_delta: .legs[0].delta}'   /tmp/ideas_equity_put_live.json
```

Expected:

- `short_delta` and `legs_delta` are **non-null** and equal for the short leg.
- `dte` and `width` still respect the human rules (45±5 DTE, correct width according to `by_price_bracket`).

If live mode is not available (network/DNS/429/etc.), Codex must document this and rely on mock-mode verification.

### 3.5 E2E Check — Human-readable CLI

Run:

```bash
export STRATDECK_DATA_MODE=mock

python -m stratdeck.cli trade-ideas   --universe tasty_watchlist_chris_historical_trades   --strategy short_put_spread_equity_45d   --max-per-symbol 1
```

Codex must ensure:

- Human-readable output still shows:
  - Correct rationale lines.
  - Correct legs (short/long, strikes, expiries).
- Any temporary debug lines (e.g., `[DEBUG] leg delta: ...`) are removed before finalizing the branch.

---

## 4. Self-Check & Self-Correction Loop

Codex must follow this loop until all done criteria are satisfied:

1. **Apply the minimal change** to make `legs` canonical and `short_legs` / `long_legs` derived views, without breaking existing semantics.

2. Run unit tests:

   ```bash
   pytest -q
   ```

   - If failing:
     - Read errors.
     - Fix only what is required to align tests with the new invariants (or adapt code to satisfy existing tests).
     - Re-run until green or until there is a clear environmental blocker.

3. Run the **mock-mode JSON CLI check** (§3.3).

   - If `short_delta` is `null` or mismatched vs `legs_delta`:
     - Re-open the planner and model.
     - Verify `short_legs` and `long_legs` are derived from the same `TradeLeg` instances as `legs`.
     - Confirm `delta` is set upstream on those leg instances.
     - Fix and loop back to step 2.

4. If live data is accessible, run the **live-mode JSON CLI check** (§3.4) and ensure consistency.

5. Run the **human-readable CLI check** (§3.5) and confirm no regressions in format or content (aside from improved internal consistency).

6. Only when all checks pass (tests + mock JSON + optional live JSON + human-readable) may Codex consider the task complete.

Codex must not leave the branch with failing tests, null deltas in `short_legs`, or visibly broken CLI output.

---

## 5. Done Criteria

Codex can mark this task as done only when:

1. `pytest -q` passes with all tests green, including the new test(s) for leg consistency.
2. The mock-mode JSON command:

   ```bash
   export STRATDECK_DATA_MODE=mock

   python -m stratdeck.cli trade-ideas      --universe tasty_watchlist_chris_historical_trades      --strategy short_put_spread_equity_45d      --max-per-symbol 1      --json-output > /tmp/ideas_equity_put_mock.json

   jq '.[] | {symbol, dte, width: .spread_width, short_delta: .short_legs[0].delta, legs_delta: .legs[0].delta}'      /tmp/ideas_equity_put_mock.json
   ```

   shows **non-null** `short_delta` and `legs_delta` values, and they are equal for the short leg.

3. If live data is available, the analogous live-mode command produces the same invariants.

4. The human-readable `trade-ideas` CLI output is unchanged apart from any invisible internal consistency fixes (no debug noise, same leg listing).

5. `TradeIdea.legs`, `TradeIdea.short_legs`, and `TradeIdea.long_legs` are aligned by construction going forward, so any future fields added to `TradeLeg` (e.g., more greeks) will automatically flow through to all three lists when using `model_dump(mode="json")`.

6. Codex leaves a short comment (where appropriate) in the planner or model, documenting that `legs` is the canonical list and `short_legs` / `long_legs` are derived views, explicitly to avoid drift in fields like `delta` and `dte`.
