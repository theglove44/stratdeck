# PROJECT: StratDeck Agent System – Phase 1 Trade-Ideas Stabilisation

## Overview

You are working in the `stratdeck-copilot` repo. The goal of this task is to **fully complete Phase 1** of the “trade-ideas” pipeline stabilisation for the StratDeck Agent System.

This means:

1. Underlying price handling is robust and tested (including fallbacks and 429/Network resilience on chains/quotes).
2. Strategy provenance is **first-class** data on every `TradeIdea`, not just a note string.
3. Strategy filters (IVR, POP, credit-per-width, etc.) both **gate** ideas and expose **explainable metadata** on the idea objects.
4. The CLI has a clear contract for where “last trade ideas” live (stdout JSON and/or `.stratdeck/last_trade_ideas.json`) and this is enforced by tests.
5. All new behaviour is covered by unit/integration tests that can be run via `pytest`.

Treat this as a “Phase 1 done-done” task. When it’s complete, Chris should be able to rely on the trade-ideas output as a stable, inspectable input to later paper-trading and agent layers.

---

## Context / Current State (what you should assume)

- Repo: `stratdeck-copilot` (Python 3.9, Pydantic v2).
- Core CLI entry point: `python -m stratdeck.cli ...`.
- Key commands for this task:
  - `python -m stratdeck.cli trade-ideas --universe index_core --strategy short_put_spread_index_45d --json-output`

- Relevant modules (names from existing code – do **not** rename them):
  - `stratdeck/agents/trade_planner.py`
    - Builds `TradeIdea` objects.
    - Handles `underlying_price_hint`, POP, credit-per-width, etc.
    - Applies strategy filters before emitting ideas.
  - `stratdeck/strategy_engine.py`
    - Contains `StrategyUniverseAssignment`, `SymbolStrategyTask`, etc.
    - Knows which strategy/universe produced a given idea.
  - `stratdeck/strategies.py`
    - Pydantic models for `StrategyTemplate`, `StrategyFilters`, etc.
  - `stratdeck/config/strategies.yaml`
    - YAML config defining universes, strategies, and their filters (`min_ivr`, etc.).
  - `stratdeck/tools/chain_pricing_adapter.py` and/or `stratdeck/tools/chains.py`
    - Responsible for fetching / pricing option chains from Tastytrade (or abstractions over it).

- JSON shape of trade ideas (current, approximate):
  - Already includes fields like `symbol`, `data_symbol`, `trade_symbol`, `strategy` (shape name), `direction`, `vol_context`, `rationale`, `legs`, `underlying_price_hint`, `dte_target`, `spread_width`, `target_delta`, `pop`, `credit_per_width`, `estimated_credit`.
  - Provenance is currently only present in a **note string**, e.g. `[provenance] template=short_put_spread_index_45d universe=index_core`.

- Filters:
  - `StrategyFilters` model supports `min_ivr`, `max_ivr`, `min_pop`, `max_pop`, `min_credit_per_width`, etc.
  - The planner calls an internal `_passes_strategy_filters(candidate, strategy)` which already gates ideas, but **ideas themselves don’t expose which filters were applied or why they passed**.

- Underlying price hints:
  - `underlying_price_hint` is produced in the planner via a helper (e.g. `resolve_underlying_price_hint(...)`).
  - For `index_core` strategies with SPX/XSP, we expect hints like `660.3` for XSP and `6607.3` for SPX, with matching strikes in the chain data.
  - There have been 429 “Too Many Requests” errors coming from Tastytrade when fetching chains/quotes. We want to handle these gracefully.

- Tests:
  - There are already tests around XSP strike scaling (e.g. `tests/test_xsp_strike_scaling.py`) which are passing. These should **not** be broken.

---

## High-Level Objectives

1. **Stabilise underlying price & chain fetching**
   - Make `underlying_price_hint` robust and predictable.
   - Ensure chain/quote fetching handles 429 and transient failures without crashing the whole scan.

2. **Make strategy provenance explicit**
   - Every `TradeIdea` must clearly state which strategy config and universe produced it (machine-readable fields).

3. **Make filters explainable**
   - Each emitted idea should carry filter metadata (`filters_passed`, thresholds, etc.) so we can see why it passed.

4. **Clarify “last trade ideas” contract**
   - Decide and implement how `trade-ideas` persists the last run (stdout JSON + `.stratdeck/last_trade_ideas.json`) and test it.

5. **Add tests and keep things green**
   - Add targeted unit tests + small integration tests for the above without blowing up the test suite.

---

## Scope of This Task

**In-scope**

- `trade-ideas` command behaviour (data correctness + JSON output).
- `TradeIdea` data model (adding fields, not breaking existing ones).
- Underlying price hint resolution and fallback behaviour.
- Strategy filter application logic + metadata on the idea objects.
- Chain/quote fetching retry/backoff around 429 / transient errors.
- Persistence and tests for “last trade ideas” (`.stratdeck/last_trade_ideas.json`).

**Out-of-scope**

- Any orchestrator / auto-trading loop (`orchestrator.py`, paper/live trading).
- New strategies or universes.
- Any UI work.
- Changing the external CLI interface for `trade-ideas` (except adding new, optional flags/env vars).

---

## Workstream 1 – Underlying Price Hint & Chain Robustness

### Goals

- Ensure `underlying_price_hint` is:
  - Sourced from a single, well-defined helper/adapter.
  - Correct for SPX + XSP (no scaling bugs).
  - Robust to failures in live data (e.g. 429 / network issues).
- Ensure chain/quote fetching does **not** crash the entire `trade-ideas` run on transient errors; symbols can be skipped with clear logs.

### Requirements

1. **Centralised hint resolution**
   - Implement (or confirm and harden) a single helper, e.g.:

     ```python
     class UnderlyingPriceAdapter:
         def get_hint(self, symbol: str) -> float | None:
             ...
     ```

     or an equivalent function in the planner layer.
   - Resolution order must be:
     1. Live quote (Tastytrade live quote / chain mid).
     2. Recently cached quote (if such cache exists already).
     3. EOD / historical price (existing TA / chartist logic).
     4. If all sources fail: **return `None` and log a warning**, do **not** raise.

   - All `underlying_price_hint` usages must go through this logic – no duplicate price-fetch logic scattered elsewhere.

2. **429 / network resilience for chains/quotes**
   - Wrap the Tastytrade chain/quote calls in a small retry/backoff helper:
     - On HTTP 429 or transient network errors:
       - Retry a **small** number of times (e.g. 2–3) with exponential backoff.
       - After retries are exhausted, log a structured warning and **skip** that symbol, rather than failing the entire run.
     - On non-recoverable errors (e.g. invalid symbol/config), fail fast with a clear error message.

   - This should apply to both:
     - The place where you pull chains (for POP/credit-per-width computation).
     - The place where you get underlying prices (if those are separate calls).

3. **No scaling bugs for SPX/XSP**
   - There must be **no symbol-specific strike scaling hacks** (e.g. dividing/multiplying XSP by 10) in the current implementation.
   - Any existing tests around XSP scaling must remain green (`tests/test_xsp_strike_scaling.py`, etc.).

### Tests / Acceptance Criteria

- Add unit tests around the hint resolver:
  - **Happy path**: live provider returns a quote → that value is used.
  - **Live error**: provider raises a 429-like error → TA/historical fallback is used; no exception escapes.
  - **No data**: all sources fail → helper returns `None` and logs a warning (you don’t need to over-test logging, but at least assert that it returns `None`).

- Add tests (unit or integration) for chain fetching:
  - Mock Tastytrade chain/quote call to raise a 429 twice then succeed:
    - The helper should retry and eventually succeed.
  - Mock persistent 429 / network failure:
    - The symbol should be skipped with a logged warning.
    - Other symbols in the universe should still be processed.

- Running:

  ```bash
  python -m stratdeck.cli trade-ideas         --universe index_core         --strategy short_put_spread_index_45d         --json-output
  ```

  must **not** crash due to 429s and must still produce reasonable `underlying_price_hint` values for the symbols that have data (especially SPX/XSP).

---

## Workstream 2 – Strategy Provenance on TradeIdea

### Goals

- Every `TradeIdea` must expose **machine-readable** provenance:
  - Which strategy config (template) produced it.
  - Which universe it came from.

### Requirements

1. **New fields on `TradeIdea`**

   In `stratdeck/agents/trade_planner.py` (or wherever the Pydantic model is defined), extend the idea model with at least:

   ```python
   strategy_id: str | None = None   # e.g. "short_put_spread_index_45d"
   universe_id: str | None = None   # e.g. "index_core"
   ```

   You may also add an optional `task_id` or `provenance` dict if useful, but keep it simple.

2. **Populate from `SymbolStrategyTask` / config**

   When building `TradeIdea` instances from `SymbolStrategyTask` (or equivalent), set:

   ```python
   idea.strategy_id = task.strategy.name   # or the appropriate config key
   idea.universe_id = task.universe.name   # or equivalent
   ```

   - The values must match the identifiers defined in `stratdeck/config/strategies.yaml` (e.g. `short_put_spread_index_45d`, `index_core`).
   - Do **not** rely on parsing the provenance note string – use the actual config objects you already have.

3. **Keep human-readable notes, but make them redundant**

   - If there is already a provenance note line (e.g. `[provenance] template=... universe=...`), keep it for now, but it should be redundant. The primary source of truth is `strategy_id` / `universe_id` fields.

### Tests / Acceptance Criteria

- Add a small test that runs the planner for `index_core` + `short_put_spread_index_45d` and inspects at least one resulting idea (via JSON) to assert:
  - `strategy_id == "short_put_spread_index_45d"`
  - `universe_id == "index_core"`

- Existing tests around trade ideas must remain green.

---

## Workstream 3 – Filter Metadata & Explainability

### Goals

- Filters (IVR, POP, credit-per-width, etc.) should already **gate** ideas (they do).
- We now also want **metadata** on each idea capturing:
  - Whether it passed filters.
  - Which thresholds were applied.
  - (Optionally) textual reasons.

### Requirements

1. **Extend `TradeIdea` with filter metadata**

   Add the following fields to `TradeIdea`:

   ```python
   filters_passed: bool | None = None
   filters_applied: dict[str, float] | None = None
   filter_reasons: list[str] | None = None
   ```

   - `filters_applied` should be a mapping of threshold name to numeric value (only include keys that are not `None`), e.g.:
     - `{"min_ivr": 0.2, "min_credit_per_width": 0.25}`.

2. **Populate metadata for **emitted** ideas**

   - Wherever `_passes_strategy_filters(candidate, strategy)` is used to decide whether to emit an idea:
     - If it **passes**:
       - Build a `TradeIdea` and set:
         - `filters_passed = True`
         - `filters_applied` with the active thresholds from `strategy.filters`.
         - `filter_reasons = []` (or some optional positive descriptions if you wish).
     - If it **fails**:
       - Do not emit a `TradeIdea` (current behaviour).
       - Ensure there is a clear log line explaining why (e.g. `min_ivr 0.18 < 0.30`).

   - You may factor out filter evaluation into a helper like:

     ```python
     @dataclass
     class FilterResult:
         passed: bool
         reasons: list[str]
     ```

     but keep the API simple and internal.

3. **Debug logging toggle (optional but encouraged)**

   - If an env var like `STRATDECK_DEBUG_STRATEGY_FILTERS=1` is set, print a one-liner for every candidate, e.g.:
     - Passed:
       - `[filters] SPX short_put_spread_index_45d PASSED: ivr=0.30 >= 0.20, credit_per_width=0.28 >= 0.25`
     - Failed:
       - `[filters] SPX short_put_spread_index_45d FAILED: min_ivr 0.18 < 0.30`

### Tests / Acceptance Criteria

- Add a test for a strategy with `min_ivr` (and optionally `min_credit_per_width`) that ensures:

  - For a candidate with `ivr` **below** threshold:
    - It is **not** emitted as a `TradeIdea`.
  - For a candidate with `ivr` **above** threshold:
    - It **is** emitted, and in the resulting JSON:
      - `filters_passed == True`
      - `filters_applied["min_ivr"] == <configured threshold>`
      - `filter_reasons` is present (even if empty).

- Existing behaviour (that filters actually “bite”) must remain – we are **adding metadata**, not changing gating semantics.

---

## Workstream 4 – “Last Trade Ideas” Persistence Contract

### Goals

- Clarify and enforce how `trade-ideas` persists the last generated ideas so that other tools (like paper-trade entry) have a stable place to read from.
- Ensure stdout JSON and persisted file (if any) are consistent.

### Requirements

1. **Decide & implement the contract**

   Implement the following behaviour for `trade-ideas`:

   - When `--json-output` is used:
     - Emit the full list of ideas as JSON to **stdout** (this is already in place).
     - **Also** write the same JSON array to `.stratdeck/last_trade_ideas.json` by default.
   - When `--json-output` is **not** used:
     - Keep existing human-readable output behaviour.
     - It is acceptable (and preferable) to still write `.stratdeck/last_trade_ideas.json` with the last full JSON ideas list.

   To avoid surprises, you may add a new optional env var to disable file writing, e.g. `STRATDECK_DISABLE_LAST_TRADE_IDEAS_FILE=1`. By default it should be **enabled**.

2. **Ensure consistency between stdout & file**

   - The contents of `.stratdeck/last_trade_ideas.json` must be **byte-for-byte equivalent** to what would have been emitted to stdout when `--json-output` is used, modulo whitespace.

3. **Error handling**

   - If `.stratdeck` directory cannot be created, or the file cannot be written:
     - Log a warning.
     - Do **not** crash the `trade-ideas` command (stdout JSON is more important).

### Tests / Acceptance Criteria

- Add a test (can be an integration-style test using a temp directory) that:
  - Runs `trade-ideas` with `--json-output` redirected to a temp file (e.g. `/tmp/ideas.json`).
  - Asserts that `.stratdeck/last_trade_ideas.json` exists under the test’s working directory.
  - Asserts that parsing both files as JSON arrays yields the same data (same length, and same elements by value).

- Ensure that if `.stratdeck` cannot be created in the current working directory, the command still exits successfully, but logs a clear warning.

---

## Non-Functional Constraints

- Keep changes **backwards compatible** for existing callers of `trade-ideas`.
- Do **not** significantly increase run time for the common case; retries should be bounded and only triggered on 429/Network errors.
- Keep the code clear and well-factored; small private helpers are fine if they improve readability.

---

## What Not to Change

- Do **not** change:
  - CLI flags or positional arguments for existing commands (except adding new optional flags/env vars where explicitly called out).
  - Names/paths of core modules (`trade_planner.py`, `strategies.py`, `strategy_engine.py`, etc.).
  - Existing XSP scaling tests – they must still pass.

- Do **not** introduce new external dependencies without a clear reason.

---

## How to Run & Validate (for you / for Chris)

From the repo root, after your changes:

```bash
# 1) Run tests
pytest -q
```

```bash
# 2) Sanity check – trade ideas output
export STRATDECK_DATA_MODE=live

python -m stratdeck.cli trade-ideas       --universe index_core       --strategy short_put_spread_index_45d       --json-output > /tmp/ideas.json
```

- Confirm:
  - `/tmp/ideas.json` parses as a JSON array.
  - Each idea has `strategy_id`, `universe_id`.
  - Each idea has `filters_passed`, `filters_applied`, `filter_reasons`.
  - `underlying_price_hint` looks sane for SPX/XSP (e.g. `~660` and `~6600` region).

```bash
# 3) Check last_trade_ideas file
jq '.[].underlying_price_hint' /tmp/ideas.json
jq '.[].underlying_price_hint' .stratdeck/last_trade_ideas.json
```

- The lists printed by both commands should match.
- The `.stratdeck/last_trade_ideas.json` file should be updated on each run.

When all acceptance criteria in the workstreams above are satisfied and tests are green, Phase 1 is considered **complete**.
