# Codex-Max Task: Fix XSP Strike / Credit Scaling Bug

## Task Summary

The `short_put_spread_index_45d` strategy is producing nonsense economics for `XSP` trades in `trade-ideas`. The spread width and/or strike scaling for `XSP` is wrong, resulting in an absurdly low `estimated_credit` and `credit_per_width` compared to what a realistic 45D short put spread in `XSP` should look like.

This task is for Codex-Max to:

- Reproduce the bug.
- Identify where XSP strike / premium scaling is going wrong.
- Implement a robust fix so that `XSP` index spreads have sane width and credit metrics.
- Add regression tests that lock in correct behaviour for both `SPX` and `XSP`.

---

## Repository / Branch / Stack

- **Repo:** `stratdeck-copilot`
- **Language:** Python 3.9
- **Key Dependencies:** Pydantic v2, Tastytrade SDK (for live data), internal `ChainPricingAdapter`
- **CLI Entrypoint:** `python -m stratdeck.cli ...`
- **Default branch for this task:** `main` (unless an `XSP`-specific feature branch already exists; if so, base on that but keep the task self-contained).

---

## Context

We have an index strategy `short_put_spread_index_45d` that runs over the `index_core` universe (at minimum, `SPX` and `XSP`). The idea planner + chain pricing stack are supposed to:

- Build a defined-risk short put vertical spread for each index.
- Target ~45 DTE.
- Compute core metrics such as `spread_width`, `estimated_credit`, `credit_per_width`, and `pop`.

This generally works for `SPX` but is clearly broken for `XSP`. That’s showing up in the generated JSON in `.stratdeck/last_trade_ideas.json`.

We already know from other work:

- Strategy + universe config live in `stratdeck/config/strategies.yaml`.
- Strategy models are in `stratdeck/strategies.py` and wired via `stratdeck/strategy_engine.py`.
- Chain construction and pricing flow through `stratdeck/tools/chains.py` and `stratdeck/tools/chain_pricing_adapter.py`.
- There are existing tests around live pricing and underlying hints in:
  - `tests/test_live_data_adapter.py`
  - `tests/test_tasty_chains_live.py`
  - `tests/test_trade_planner_underlying_price.py`
  - `tests/test_underlying_price_hint.py`

This task focuses on the **XSP-specific bug** in that pipeline.

---

## Current (Broken) Behaviour

### How to Reproduce

From an activated virtualenv in `stratdeck-copilot`:

1. Ensure your `.env` and data mode are set for live data (or the same mode used when the bug was originally observed):

    - `export STRATDECK_DATA_MODE=live`
    - Source `.env` if needed so that Tastytrade credentials and other env vars are loaded.

2. Run the index 45D scan:

    - `python -m stratdeck.cli trade-ideas --strategy short_put_spread_index_45d --universe index_core --json-output`

3. After it runs, inspect the most recent trade ideas:

    - `cat .stratdeck/last_trade_ideas.json`

4. For the `XSP` idea, we previously observed a payload of the form (simplified via `jq`):

    - `jq '.[0] | {symbol, strategy, spread_width, dte_target, pop, credit_per_width, estimated_credit}' .stratdeck/last_trade_ideas.json`

    Outputs something along the lines of:

    ```json
    {
      "symbol": "XSP",
      "strategy": "short_put_spread",
      "spread_width": 5.0,
      "dte_target": 45,
      "pop": 0.56,
      "credit_per_width": 0.002,
      "estimated_credit": 0.01
    }
    ```

The exact numbers will move with the market, but core symptoms are:

- The spread width for XSP is being treated as **5.0** (index points).
- The **estimated credit** is around **0.01** (i.e. 1 cent) for a $5-wide spread.
- `credit_per_width` is around **0.002** (i.e. 0.2% of width), which is totally unrealistic.

For a defined-risk index spread, we expect:

- A non-trivial credit for a $5-wide or $1-wide spread (tens of cents at bare minimum, not 1 cent).
- `0 < credit_per_width <= 1.0` and in a broadly similar order of magnitude for `SPX` and `XSP` (after accounting for notional differences).

The key bug: **XSP’s economics are being scaled down by roughly an order of magnitude** compared to what they should be.

---

## Hypotheses

You should confirm or falsify these – they are not instructions, but hints:

1. **Strike scaling / multiplier bug for XSP**  
   Somewhere in the index handling logic, XSP is treated as a 1/10 scale of SPX and is being **double-scaled**:
   - Either the underlying or premium for XSP is being divided by 10 (or otherwise re-based) more than once.
   - Or an index multiplier (100 vs something else) is being mishandled for XSP.

2. **Width rule not symbol-aware**  
   The `spread_width` rule for `short_put_spread_index_45d` might be blindly applying a `5.0` point width to both SPX and XSP, when we probably want different defaults:
   - `SPX`: 5-point width (standard)
   - `XSP`: 1-point width is more natural (but this is a design choice; the bigger issue is the *credit* being nonsensical).

3. **Symbol metadata missing/incorrect**  
   The symbol metadata for XSP in whatever mapping the engine uses (e.g. tick size, point value, multiplier) could be wrong or missing, causing:

   - Wrong translation from option quote (in dollars) to credit per width.
   - Wrong strike spacing selection vs intended `spread_width` rule.

---

## Requirements

### 1. Fix XSP Strike / Credit Scaling

- Locate all index- and symbol-specific scaling logic for SPX/XSP in:

  - `stratdeck/config/strategies.yaml` (for `short_put_spread_index_45d`)
  - `stratdeck/strategies.py`
  - `stratdeck/strategy_engine.py`
  - `stratdeck/tools/chains.py`
  - `stratdeck/tools/chain_pricing_adapter.py`
  - Any symbol metadata / mapping utilities used for index options.

- Make sure that for XSP:

  - The selected spread strikes and width in index points match the configured rule for `short_put_spread_index_45d`.
  - Premiums and credits are **not** arbitrarily divided or multiplied by 10 (or any other factor) beyond what the correct contract specs require.
  - `estimated_credit` for a short put spread:
    - Is computed in the same units as for SPX (dollars per contract).
    - Is internally consistent with `spread_width` and `credit_per_width`:

      `credit_per_width ≈ estimated_credit / spread_width` within a small epsilon.

- The implementation must avoid scattering ad-hoc fixes. If you need symbol-specific settings (e.g. width overrides, multipliers, tick sizes), centralise them in a clear, documented place (config or a small helper module).

### 2. Preserve and Improve Strategy Config Clarity

- In `stratdeck/config/strategies.yaml`, ensure the definition of `short_put_spread_index_45d` is unambiguous about:

  - Target DTE
  - Intended spread width behaviour
  - Any symbol-specific overrides (if you introduce them)

- If you decide to add symbol-specific width rules (e.g. `SPX: 5.0`, `XSP: 1.0`), wire them through via the strategy engine and document them in the YAML rather than hard-coding inside the tools layer.

### 3. Regression Tests

Create or update tests to lock in correct behaviour:

- Add a dedicated test module (suggested name):

  - `tests/test_xsp_strike_scaling.py`

- In this test:

  - Use **fixed, synthetic chain data** (not live Tasty data) so that the behaviour is deterministic.
  - Construct sample SPX and XSP option chains for a 45D scenario with similar deltas and sensible quotes.
  - Drive the same code path the CLI uses when building a `short_put_spread_index_45d` idea (via the internal API, not by shelling out).

- Assert invariants for XSP:

  - `spread_width` is equal to the configured value for XSP (1.0 or 5.0, depending on the chosen design – but it must match config).
  - `0 < estimated_credit <= spread_width` (in dollars per contract).
  - `0 < credit_per_width <= 1.0`.
  - `abs(estimated_credit / spread_width - credit_per_width) < 1e-6`.

- Add or extend a test to compare SPX vs XSP economics:

  - For synthetic SPX and XSP chains with comparable moneyness and volatility, assert that the **ratio** of `credit_per_width` between SPX and XSP is in a sane range (e.g. within a factor of 0.5–2x, configurable in the test).
  - The aim is simply to catch future 10x scaling bugs, not to enforce perfect market parity.

- If there are already tests around index strategies that include SPX but not XSP, extend them to cover XSP as well.

### 4. Manual Sanity Check via CLI

After the fix and tests:

- Re-run:

  - `python -m stratdeck.cli trade-ideas --strategy short_put_spread_index_45d --universe index_core --json-output`

- Inspect `.stratdeck/last_trade_ideas.json` for the `XSP` entry and confirm manually that:

  - The spread width is what you expect for XSP according to strategy config.
  - `estimated_credit` and `credit_per_width` are non-trivial (not 0.01 for a multi-point spread).
  - SPX and XSP credit-per-width values are broadly in the same ballpark (after accounting for notional differences), and there is no obvious 10x discrepancy.

Document this run (command + sample idea JSON) in the PR description or in a short dev note under `dev/codex/` if that’s the convention.

---

## Implementation Notes / Constraints

- **Do not break non-index underlyings.**  
  Any fix should be scoped to index strategies or symbol-specific logic, not risk/regression for equities/ETFs.

- **Keep symbol logic centralised.**  
  If SPX and XSP need special handling (e.g. width defaults, deltas, or scaling), place that logic in a single, well-documented module or config section rather than sprinkling symbol conditionals everywhere.

- **Respect existing env flags.**  
  We already have debug flags like `STRATDECK_DEBUG_STRATEGY_FILTERS` and `STRATDECK_DEBUG_TRADER_RANKING`. If there is or should be an index/symbol debug hook, consider adding one, but keep it simple.

---

## Definition of Done

1. Running the CLI index scan reproduces **sane** XSP economics:

   - For `short_put_spread_index_45d` on the `index_core` universe, the `XSP` entry in `.stratdeck/last_trade_ideas.json` shows:

     - Correct `spread_width` according to strategy config.
     - Realistic `estimated_credit` and `credit_per_width` (not effectively 0).

2. The root cause of the XSP scaling bug is identified and fixed in a **single, well-contained** part of the codebase (e.g. symbol metadata, chain pricing adapter, or width rule wiring), with clear comments where necessary.

3. New or updated tests:

   - `tests/test_xsp_strike_scaling.py` (or equivalent) added, passing.
   - Any updated SPX/XSP comparison tests passing.
   - Entire test suite passes.

4. The strategy config (`stratdeck/config/strategies.yaml`) remains clear and does not require guesswork to understand how XSP index spreads are constructed.

5. A short note describing:

   - The cause of the bug.
   - The fix.
   - Before/after example output for XSP.

   is included either in the PR description or in a small Markdown note under `dev/codex/` that references this task.

---

## Out of Scope

- Changing the overall 45D targeting logic or filter stack.
- Implementing new strategies or orchestrator behaviour.
- Adding new data providers or altering the Tastytrade integration, except where strictly necessary to resolve the XSP scaling bug.
