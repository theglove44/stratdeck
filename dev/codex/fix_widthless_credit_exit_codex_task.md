# PROJECT: StratDeck Agent System – Fix credit-based exits for widthless short-premium trades

## Background / Bug

Phase 3 introduced a configurable exit engine that uses `ExitRulesConfig.profit_target_basis` to decide how to evaluate profit targets:

- `profit_target_basis="credit"` → target % of **initial credit**.
- `profit_target_basis="max_profit"` → target % of **max profit**.

For **widthless short-premium strategies** (e.g. short strangles, some ratio spreads):

- `compute_position_metrics` calls `_defined_risk_bounds` to derive `max_profit_total` / `max_loss_total`.
- For undefined-risk trades (no spread width), `_defined_risk_bounds` returns no width, so `max_profit_total` is left as `None`.
- `evaluate_exit_rules` currently gates the **credit-based** profit target on `metrics.max_profit_total`:

  ```python
  if rules.profit_target_basis == "credit":
      if metrics.max_profit_total and metrics.max_profit_total > 0:
          profit_pct = metrics.unrealized_pl_total / metrics.max_profit_total
          ...
  ```

Result:

- For widthless short-premium strategies with `profit_target_basis="credit"`, `max_profit_total` remains `None`.
- The credit-based rule never runs, so these positions **never close at 50% credit**.
- They only close at:
  - 21 DTE via DTE rule, or
  - Manual close.

We want:

> For widthless short-premium trades that use **credit-based profit targets**, treat **initial credit** as the effective “max profit total” so 50% (or other) credit exits still fire.

---

## Goals

1. **Derive `max_profit_total` from entry credit** for widthless short-premium strategies that use `profit_target_basis="credit"`.
2. Keep behaviour for **defined-risk spreads** unchanged.
3. Do **not** change existing exit rule semantics for `profit_target_basis="max_profit"`.
4. Ensure credit-based exits fire correctly for:
   - Short strangles (undefined risk).
   - Ratio spreads or any future widthless short-premium where we still track entry credit.

---

## Target Files

- `stratdeck/tools/position_monitor.py`
  - `compute_position_metrics(...)`
- `tests/test_position_metrics.py`
- `tests/test_exit_rules.py` (or add a new test module if more appropriate)

No changes should be required in:

- `stratdeck/config/exits.yaml`
- `stratdeck/tools/positions.py`
- `stratdeck/cli.py`

except if tests or type hints need trivial alignment.

---

## Implementation Details

### 1. Adjust `compute_position_metrics` for widthless credit-based strategies

In `stratdeck/tools/position_monitor.py`, `compute_position_metrics` currently:

- Calls `_defined_risk_bounds(...)` (or equivalent helper) to compute:

  ```python
  max_profit_per_contract, max_loss_per_contract = _defined_risk_bounds(...)
  ```

- Then computes totals:

  ```python
  max_profit_total = (
      max_profit_per_contract * contract_mult * qty
      if max_profit_per_contract is not None
      else None
  )
  max_loss_total = (
      max_loss_per_contract * contract_mult * qty
      if max_loss_per_contract is not None
      else None
  )
  ```

**Change required:**

After calling `_defined_risk_bounds(...)` but before computing totals, **inject a fallback** for undefined-width, credit-based strategies:

- Condition:

  - `max_profit_per_contract is None` (no defined width / undefined risk), **and**
  - `exit_rules.profit_target_basis == "credit"` **and**
  - `exit_rules.is_short_premium` (to keep behaviour narrow and explicit).

- Behaviour:

  - Treat **initial credit** as the synthetic “max profit per contract”.
  - For options, initial credit per contract is `entry_mid * contract_mult`.

**Pseudo-code (to be adapted to actual names and structure):**

```python
exit_rules = load_exit_rules(position.strategy_id)
qty = position.qty
contract_mult = 100  # or use existing constant

# After _defined_risk_bounds(...) call and before computing totals:
if max_profit_per_contract is None and exit_rules.profit_target_basis == "credit" and exit_rules.is_short_premium:
    # Use initial credit as synthetic max profit for credit-based exits
    # entry_mid is price per spread/position unit
    credit_per_contract = position.entry_mid * contract_mult

    # Only apply if credit is positive and meaningful
    if credit_per_contract > 0:
        max_profit_per_contract = credit_per_contract
        # Leave max_loss_per_contract as-is (likely None for undefined risk)
```

Then the existing totals logic continues to work:

```python
max_profit_total = (
    max_profit_per_contract * qty
    if max_profit_per_contract is not None
    else None
)
# Note: if your current code multiplies by contract_mult again here,
# Codex should adjust so that you don't double-count the multiplier.
# Ensure units stay consistent: if max_profit_per_contract already includes
# the contract multiplier, do NOT multiply by contract_mult again.
```

**Important consistency point (Codex must check existing code):**

- Right now, `unrealized_pl_per_contract` is computed something like:

  ```python
  unrealized_pl_per_contract = (entry_mid - current_mid) * contract_mult  # for credit trades
  unrealized_pl_total = unrealized_pl_per_contract * qty
  ```

- For the denominator in the credit-based rule to be consistent, `max_profit_total` must be in the **same units** as `unrealized_pl_total`.

So Codex must:

- Inspect how `max_profit_per_contract` and `max_profit_total` are defined.
- Ensure that when we set `max_profit_per_contract = credit_per_contract`, the total matches:

  ```python
  max_profit_total == entry_mid * contract_mult * qty
  ```

and that `unrealized_pl_total / max_profit_total` is numerically the **fraction of initial credit realised**.

If the existing code multiplies by `contract_mult` both in `unrealized_pl_per_contract` and again in `max_profit_total`, Codex must keep everything aligned and not double-multiply.

---

### 2. Keep `evaluate_exit_rules` logic unchanged, but make it now usable for widthless trades

The current credit-based logic in `evaluate_exit_rules` is fine as long as `metrics.max_profit_total` is populated:

```python
if rules.profit_target_basis == "credit":
    if metrics.max_profit_total and metrics.max_profit_total > 0:
        profit_pct = metrics.unrealized_pl_total / metrics.max_profit_total
        if profit_pct >= rules.profit_target_pct:
            action = "exit"
            reason = "TARGET_PROFIT_HIT"
            ...
```

Once `compute_position_metrics` sets `metrics.max_profit_total` for widthless short-premium trades, this code:

- Will now correctly compute `profit_pct` as **P&L / initial credit**, even when `spread width` is not known.
- Will trigger exits at the configured fraction (e.g. `profit_target_pct=0.5` for 50% of credit).

**Do not change** the `evaluate_exit_rules` structure beyond what’s needed for test adjustments; the fix is primarily in the metrics layer.

---

## Tests / Acceptance Criteria

### 1. New unit test for `compute_position_metrics` (widthless credit-based case)

Add a test to `tests/test_position_metrics.py` (or similar module) that:

- Creates a synthetic `PaperPosition` representing a **short strangle** or widthless short-premium trade:

  - `strategy_id` that maps (via `exits.yaml`) to:
    - `strategy_family: "short_strangle"` (or equivalent)
    - `is_short_premium: true`
    - `profit_target_basis: "credit"`
    - `profit_target_pct: 0.5` (50%)
  - `entry_mid = 1.00` (per spread/position unit)
  - `qty = 1`
  - `status="open"`

- Mocks pricing and vol:

  - `underlying_price` arbitrary.
  - `current_mid = 0.50` (i.e. half the entry credit).
  - DTE > 21 to avoid DTE rule interference.
  - Some `ivr` value above 20, to avoid IVR soft exit complication.

- Mocks `_defined_risk_bounds` (or whatever helper) to return **no width** / `None` for `max_profit_per_contract`.

- Calls `compute_position_metrics(...)`.

**Assertions:**

- `metrics.max_profit_total` is **not** `None`.
- `metrics.max_profit_total` equals `entry_mid * contract_mult * qty`:
  - For `entry_mid=1.00`, `qty=1`, `contract_mult=100`:
    - `max_profit_total == 100.0` (assuming standard options).
- `metrics.unrealized_pl_total` is `50.0` in the same units (half the initial credit).

This ensures the fallback is correctly wiring initial credit into `max_profit_total`.

### 2. New unit test for `evaluate_exit_rules` using widthless metrics

Add or extend tests in `tests/test_exit_rules.py`:

- Construct a synthetic `PositionMetrics` instance representing:

  - `max_profit_total = 100.0`
  - `unrealized_pl_total = 50.0`  (50% of credit)
  - `dte` sufficiently large (e.g. `30`) so DTE rule does **not** trigger.
  - `ivr` above any soft exit threshold (e.g. 30).
  - `is_short_premium=True`
  - `strategy_family="short_strangle"` (or similar).

- `ExitRulesConfig`:

  ```python
  rules = ExitRulesConfig(
      strategy_family="short_strangle",
      is_short_premium=True,
      profit_target_basis="credit",
      profit_target_pct=0.5,
      dte_exit=21,
      ivr_soft_exit_below=20.0,
      loss_management_style="roll_adjust",
  )
  ```

- Call `evaluate_exit_rules(metrics, rules)`.

**Assertions:**

- `decision.action == "exit"`.
- `decision.reason == "TARGET_PROFIT_HIT"`.
- `any("Profit target" in r for r in decision.triggered_rules)` is `True`.

Also add a sanity test:

- With `unrealized_pl_total = 40.0` (i.e. 40% of credit), same `max_profit_total = 100.0`:

  - The decision should be `action == "hold"` (no profit exit yet) and not mistakenly exit on DTE/IVR.

### 3. Regression checks for defined-risk spreads

Add/keep tests to ensure **defined-risk** behaviour unchanged:

- For a normal vertical credit spread where `_defined_risk_bounds` returns a width:

  - Ensure `max_profit_total` continues to match the intended basis (either:
    - `initial credit total` if that’s how it’s currently coded, or
    - actual `max profit total` if that is current semantic – Codex must keep this consistent).
  - Existing tests verifying profit % calculations for defined-risk strategies should stay green.

Codex should **not** alter the semantics for defined-risk spreads beyond what tests already assert.

---

## Done When

- For a widthless short strangle / ratio spread with:

  - `profit_target_basis="credit"`
  - `profit_target_pct=0.5`
  - `entry_mid > 0`
  - `current_mid` at 50% of entry credit
  - `DTE > 21`
  - `IVR > 20`

running:

```bash
python -m stratdeck.cli positions monitor --json-output
```

yields:

- For that position, `decision.action == "exit"` and `decision.reason == "TARGET_PROFIT_HIT"`.

And:

```bash
python -m stratdeck.cli positions close-auto --dry-run --json-output
```

shows that same widthless short-premium position as a **would-be closed** candidate due to reaching 50% of initial credit, **before** 21 DTE.

All existing tests (46+) still pass, plus new tests for:

- Widthless metric computation.
- Widthless credit-based exit trigger.
