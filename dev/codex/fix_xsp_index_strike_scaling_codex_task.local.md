# PROJECT: StratDeck Copilot – Fix XSP Index Strike Scaling

You are working on the **strategy / chain-selection** side of StratDeck, specifically how **index option chains and strikes** are handled for **SPX vs XSP**.

Currently, **XSP** short put spreads are being generated with strikes that are ~10× higher than the XSP underlying price (e.g. underlying ≈ 660, strikes ≈ 6,800+), while **SPX** ideas look correct (strikes in line with SPX ≈ 6,600). This is a **symbol / chain / scaling bug** in the index chain logic, *not* in the new underlying-price helper.

Your job in this task is to **identify and fix the XSP strike scaling bug** so that XSP spreads use correctly scaled strikes, in line with the XSP underlying price, without regressing SPX or any other symbols.

---

## Repo & Environment

- Repo root: `~/Projects/stratdeck-copilot`
- Language: Python (must remain **3.9-compatible** even if running in 3.11 locally).
- CLI entrypoint: `python -m stratdeck.cli ...`
- Tests: `python -m pytest`
- Relevant project docs:
  - `AGENTS.md` – conventions, env vars, behaviour expectations.
  - `dev/codex/` – existing Codex task specs and dev notes.

Environment hints:

- `STRATDECK_DATA_MODE`:
  - `paper` – offline / cached / simulated.
  - `live` – real / live-style market data (only when properly configured).
- Underlying-price helper has been implemented in a separate SPC and is considered correct; do **not** change its interface or semantics in this task.

---

## Problem Summary

When running:

```bash
export STRATDECK_DATA_MODE=live

python -m stratdeck.cli trade-ideas \
  --strategy short_put_spread_index_45d \
  --universe index_core \
  --json-output
```

you see something like:

- **XSP idea**:
  - `underlying_price_hint` ≈ `660.3`
  - Strikes ≈ `6829.62` / `6824.62`
  - Strikes are ≈ **10× the underlying** → wrong.

- **SPX idea**:
  - `underlying_price_hint` ≈ `6607.3`
  - Strikes ≈ `6574.32` / `6569.32`
  - Strikes are consistent with SPX price → correct.

So: **underlyings are correct for both**, but **XSP strikes are mis-scaled** (likely because XSP is being fed SPX-style strikes or chains).

---

## High-Level Goal

Make sure that:

- **XSP** uses the correct **option chain product and strike scale** so its spreads are built around the XSP underlying price (e.g. if XSP ≈ 660, strikes around that level, not ~6,800).
- **SPX** behaviour remains unchanged and correct.
- No other instruments in `index_core` (or other universes) are broken.

You will achieve this by:

1. Tracing how SPX and XSP chains/strikes are chosen.
2. Fixing symbol / chain mapping so XSP uses the appropriate chain.
3. Writing network-free tests that would have caught the original bug.
4. Verifying via CLI that XSP now looks sane in live mode.

---

## Files & Areas to Inspect First

You should focus on these:

- **Planner / agent layer**
  - `stratdeck/agents/trade_planner.py`
    - How strategies like `short_put_spread_index_45d` are turned into symbol + DTE + spread-width + delta tasks.
    - How `symbol`, `trade_symbol`, and any `data_symbol` (`^GSPC` etc.) are chosen for SPX vs XSP.

- **Chain selection / adapter**
  - `stratdeck/tools/chain_pricing_adapter.py`
    - How it maps a given symbol into a specific option chain product.
    - How it selects expiries and short/long strikes given DTE, spread width, target delta, etc.
    - Any special handling (or lack of it) for SPX vs XSP.

- **Chain building / normalisation**
  - `stratdeck/tools/chains.py`
    - How raw option chains are fetched / built / normalised.
    - How requested symbols are mapped to provider symbols/products.
    - Any index-specific code that might be reusing SPX for XSP.

You may also skim:

- `tests/test_tasty_chains_live.py`
- `tests/test_live_data_adapter.py`

to see existing tests and patterns for how chains are exercised and how to plug in fakes.

---

## Non-Goals / Out of Scope

Do **not**:

- Change CLI syntax (`stratdeck/cli.py`).
- Modify the underlying-price helper interface or semantics.
- Change risk filters, POP/IVR gating, strategy filters, or orchestrator logic.
- Add new strategies/universes.
- Introduce real network calls into unit tests.

This task is tightly scoped to **XSP strike scaling in chain / selection logic**.

---

## Tasks for Codex-Max

### Task 1 – Reproduce and Document the Bug

1. From repo root, run:

   ```bash
   export STRATDECK_DATA_MODE=live

   python -m stratdeck.cli trade-ideas \
     --strategy short_put_spread_index_45d \
     --universe index_core \
     --json-output
   ```

2. Confirm:
   - SPX idea: strikes close to `underlying_price_hint`.
   - XSP idea: strikes ≈ 10× `underlying_price_hint` (or otherwise obviously mis-scaled).

3. Copy a minimal “**BEFORE**” JSON snippet (just SPX + XSP with `symbol`, `underlying_price_hint`, and leg strikes) into a new dev note:

   - Suggested file (commit to repo):  
     `dev/codex/fix-xsp-index-strike-scaling.md`

   Include a short 1–2 sentence description of the bug.

---

### Task 2 – Trace the Data Path for SPX vs XSP

Goal: understand where XSP picks up the wrong strikes.

1. In `stratdeck/agents/trade_planner.py`:
   - Locate where `short_put_spread_index_45d` is planned.
   - Identify how the planner:
     - Chooses `symbol` (SPX/XSP) for each idea.
     - Uses any `data_symbol` (e.g. `^GSPC`).
     - Calls into the chain/pricing adapter to pick strikes.

2. In `stratdeck/tools/chain_pricing_adapter.py`:
   - Track how the adapter:
     - Maps the planned symbol to a specific option chain product.
     - Selects expiry and short/long strikes for a short put spread.
   - Look for:
     - Hard-coded SPX assumptions.
     - Places where XSP is treated just like SPX, possibly reusing SPX-based chains.

3. In `stratdeck/tools/chains.py`:
   - Inspect functions that:
     - Resolve a symbol into a provider symbol/product.
     - Build or normalise chains for indexes.
   - Look for:
     - Any reuse of SPX chains for XSP.
     - Assumptions that both SPX and XSP share a single chain representation with SPX-scale strikes.

Produce a short explanation (1–3 sentences) in the dev note describing the **root cause** you found, e.g.:

> “XSP was using the SPX chain product under the hood, so it inherited SPX-sized strikes, then we just changed the symbol to XSP.”

---

### Task 3 – Implement a Correct XSP Chain / Symbol Mapping

Implement a fix so that:

- XSP requests and uses the **correct option chain product**.
- XSP strikes are in the same **price scale** as its underlying.
- SPX and any other symbols continue to behave correctly.

Recommended approach:

1. **Introduce explicit symbol → chain mapping** (if not already present):

   - Centralise this in a small helper or mapping function in the appropriate module, e.g.:

     ```python
     def map_index_trade_symbol_to_chain_symbol(trade_symbol: str) -> str:
         mapping = {
             "SPX": "SPX",  # full S&P 500 index
             "XSP": "XSP",  # mini S&P 500 index (1/10), with its own chain
         }
         return mapping.get(trade_symbol, trade_symbol)
     ```

   - Use this when resolving chain symbols inside `chain_pricing_adapter.py` and/or `chains.py`.

2. **Remove or avoid implicit SPX-only assumptions**:

   - Ensure that XSP is not just “SPX with a different label”.
   - If the provider abstraction already supports fetching XSP chains, use that instead of trying to scale SPX.

3. **Avoid ad-hoc scaling hacks**:

   - Prefer correct chain selection over dividing strikes by 10.
   - Only introduce explicit numeric scaling if absolutely necessary and document clearly why, with tests.

4. Keep changes as small and localised as possible, with clear comments referencing this bug (e.g. “Fix XSP strike scale; previously used SPX-sized chain”).

---

### Task 4 – Add Network-Free Tests for XSP Strike Sanity

Create tests that:

- Would have **failed** under the current buggy behaviour (XSP strikes ≈ 10× underlying).
- **Pass** with the new symbol/chain mapping.

Suggested structure:

1. Create a new test file or extend an appropriate one, e.g.:

   - New: `tests/test_xsp_strike_scaling.py`  
   - Or extend: `tests/test_tasty_chains_live.py` (keeping tests offline).

2. Build **fake chains** for SPX and XSP:

   ```python
   spx_underlying = 6500.0
   spx_strikes = [6450.0, 6455.0, 6460.0]

   xsp_underlying = 650.0
   xsp_strikes = [645.0, 645.5, 646.0]
   ```

   - Wrap them into minimal contract/chain objects expected by the chain selection logic.
   - Monkeypatch or inject these into the adapter so code paths run without real network calls.

3. Add assertions:

   - **SPX sanity**:
     - Selected SPX short/long strikes come from the SPX fake chain.
     - `short_strike / spx_underlying` is within a sensible band (e.g. 0.8–1.2) for the scenario you set up.

   - **XSP sanity**:
     - Selected XSP short/long strikes come from the **XSP** fake chain.
     - `short_strike / xsp_underlying` is also in a sensible band (e.g. 0.8–1.2).
     - Critically, assert that **no 10× mismatch** is possible:

       ```python
       assert short_strike / xsp_underlying < 3.0
       ```

       (Adjust the threshold as needed, but it must be low enough that the original bug fails.)

4. Run tests:

   ```bash
   python -m pytest
   ```

   All existing tests + new tests must pass.

---

### Task 5 – Manual CLI Sanity Check After Fix

After tests pass, re-run:

```bash
export STRATDECK_DATA_MODE=live

python -m stratdeck.cli trade-ideas \
  --strategy short_put_spread_index_45d \
  --universe index_core \
  --json-output
```

Verify:

- **SPX idea**:
  - `underlying_price_hint` ≈ current SPX level.
  - Strikes are near that underlying (similar to current behaviour).

- **XSP idea**:
  - `underlying_price_hint` ≈ current XSP level (≈ 1/10 SPX).
  - Strikes are now **near XSP price**, not 10× above.
  - Spread width matches the strategy config (e.g. 5-wide in XSP units).

Copy a minimal “**AFTER**” JSON snippet (SPX + XSP with `underlying_price_hint` and strikes) into `dev/codex/fix-xsp-index-strike-scaling.md`.

---

## Validation & Completion Criteria

This Codex-Max task is complete when:

1. **Unit tests cover XSP scaling**:
   - There are tests that would fail under the original bug (10× XSP strikes) and pass under your fix.
   - Tests are network-free and use fake chains.
   - SPX sanity is also checked (no regression).

2. **All tests pass**:

   ```bash
   python -m pytest
   ```

3. **CLI behaviour is correct in live-style mode**:

   - Running:

     ```bash
     export STRATDECK_DATA_MODE=live

     python -m stratdeck.cli trade-ideas \
       --strategy short_put_spread_index_45d \
       --universe index_core \
       --json-output
     ```

     produces:
     - SPX trade idea with strikes aligned to SPX `underlying_price_hint`.
     - XSP trade idea with strikes aligned to XSP `underlying_price_hint`, with no ~10× mismatch.

4. **Code changes are clear and contained**:

   - XSP-specific behaviour is localised, documented, and easy to extend.
   - Symbol/chain mapping for SPX/XSP is explicit.
   - No unrelated logic (underlying-price helper, orchestrator, risk filters, etc.) has been modified.

Once all these conditions are met and the PR is merged, the **XSP index strike scaling bug** is considered fixed.
