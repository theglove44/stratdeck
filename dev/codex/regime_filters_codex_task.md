# Codex-Max Task — Regime-Aware Strategy Filters (Trend & Volatility)

## Repo & Branch

You are working in the **StratDeck Copilot** project:

- **Repo:** `git@github.com:theglove44/stratdeck-copilot.git`
- **Default branch:** `main`
- **Language:** Python 3.x
- **Tests:** `pytest -q`
- **CLI entrypoint:** `python -m stratdeck.cli ...`

Create and use a dedicated feature branch for this slice:

- **Branch name:** `feature/regime-aware-filters`

### Branch setup (for the human developer)

On the developer machine:

```bash
cd /Users/christaylor/Projects/stratdeck-copilot

git checkout main
git pull --ff-only origin main

git checkout -b feature/regime-aware-filters
```

All changes described below should be made on this feature branch.

---

## High-Level Goal

Introduce **regime-aware filters** that prevent obviously mismatched strategies, such as:

- Blocking **bullish put spreads** in a clear **downtrend**.
- Allowing certain strategies only when volatility is in certain regimes (e.g. short premium only in "normal" or "high" vol).

This must integrate with the **existing central filter engine** and continue the theme of:

1. **Filters that bite:** candidates that violate regime rules must be rejected.
2. **Filters that explain themselves:** the `FilterDecision` should include human-readable reasons like
   - `"trend_regime downtrend not in ['uptrend', 'sideways']"`
   - `"vol_regime low not in ['normal', 'high']"`

The end result: `trade-ideas` outputs only strategies that are consistent with both **numeric filters** (IVR, POP, credit-per-width, DTE) and **market regime constraints** (trend & volatility).

---

## Existing Context (Do Not Change Core Concepts)

The following already exists from previous slices and must be **respected**:

- A central filter engine in `stratdeck/tools/filters.py` that evaluates candidates against `StrategyFilters` and `DTERule` and returns a `FilterDecision` with:
  - `passed: bool`
  - `applied: Dict[str, float]`
  - `reasons: List[str]`

- `TradePlanner` (`stratdeck/agents/trade_planner.py`) builds a `candidate: Dict[str, Any]` containing (among others):
  - `symbol`
  - `strategy_type`
  - `direction`
  - `spread_width`
  - `dte_target`
  - `pop`
  - `ivr`
  - `credit_per_width`
  - `estimated_credit`
  - It then calls `_evaluate_strategy_filters(candidate, task.strategy)` which delegates to `evaluate_candidate_filters(...)` in the filter engine.

- Strategy configuration models (`stratdeck/strategies.py`) include:
  - `StrategyTemplate` with fields such as `name`, `label`, `applies_to_universes`, `product_type`, `order_side`, `option_type`, `dte: DTERule`, `filters: StrategyFilters`, etc.
  - `StrategyFilters` which already carries numeric constraints (like `min_ivr`, `min_pop`, `min_credit_per_width`).

- The numerical filters are already implemented and tested; this task is **additive**:
  - **Do not** change the semantics of existing numeric filters.
  - **Do not** change the public shape of `TradeIdea` JSON (`filters_passed`, `filters_applied`, `filter_reasons`, etc.).

---

## New Capability: Regime-Aware Filters

We want to make strategy activation depend on:

- **Trend regime**: e.g. `"uptrend"`, `"downtrend"`, `"sideways"`, `"unknown"`.
- **Volatility regime**: e.g. `"low"`, `"normal"`, `"high"`, `"extreme"`.

`TradePlanner` already performs technical analysis and regime detection; it derives values like `trend_regime` and `vol_regime` for each symbol/scan row. These must be **propagated into the candidate** and evaluated by the central filter engine.

### Ergonomics goals

- Regime configuration must live in strategy config (`strategies.yaml` → `StrategyTemplate`), not hard-coded in code.
- Default behaviour: if no regime constraints are configured for a strategy, **no additional regime filtering** applies.
- Constraints must be **expressive but simple**:
  - Allowed lists are enough for now:
    - `allowed_trend_regimes`
    - `allowed_vol_regimes`
  - Optional `blocked_trend_regimes` / `blocked_vol_regimes` can be added if it’s clean to implement.

---

## Task 1 — Extend Strategy Models for Regime Filters

**Goal:** Add optional regime filter fields to the strategy config models.

### 1.1 Update `StrategyTemplate` in `stratdeck/strategies.py`

Add new optional fields to `StrategyTemplate` (and associated Pydantic models):

```python
class StrategyTemplate(BaseModel):
    # ... existing fields ...

    # New (regime-aware) fields:
    allowed_trend_regimes: Optional[List[str]] = None
    allowed_vol_regimes: Optional[List[str]] = None

    # (Optional) if you want explicit block lists as well:
    blocked_trend_regimes: Optional[List[str]] = None
    blocked_vol_regimes: Optional[List[str]] = None
```

Constraints:

- Keep the fields fully optional; existing YAML must continue to parse.
- Do **not** break current strategies if these fields are missing.
- Use plain `List[str]` (no enums) for now to avoid schema churn; values will be simple strings like `"uptrend"`, `"downtrend"`, `"sideways"`, `"low"`, `"normal"`, `"high"`.

### 1.2 Wire from YAML

Strategy templates are loaded from `strategies.yaml` into `StrategyTemplate` objects. After adding the new fields, update `strategies.yaml` as needed:

- For this task, configure at least **one** or **two** strategies to use regime filters (for testability). For example:

```yaml
templates:
  short_put_spread_index_45d:
    label: "Short Put Spread (Index, 45D)"
    # ... existing fields ...
    allowed_trend_regimes: ["uptrend", "sideways"]
    allowed_vol_regimes: ["normal", "high"]
```

Keep changes minimal at first so that behaviour can be tested and reasoned about.

---

## Task 2 — Ensure Candidates Carry Regime Information

**Goal:** Make sure `candidate` dicts passed into the filter engine contain `trend_regime` and `vol_regime` (and any other useful context).

### 2.1 Update `TradePlanner._generate_for_task` (if necessary)

In `stratdeck/agents/trade_planner.py`, locate where `candidate` is constructed. Extend it to include:

```python
candidate: Dict[str, Any] = {
    "symbol": symbol,
    "strategy_type": strategy_type,
    "direction": direction,
    "spread_width": spread_width,
    "dte_target": target_dte,
    "pop": pop,
    "ivr": ivr,
    "credit_per_width": credit_per_width,
    "estimated_credit": estimated_credit,

    # New regime-related fields (assuming these are already computed earlier)
    "trend_regime": trend_regime,
    "vol_regime": vol_regime,
}
```

If `trend_regime` / `vol_regime` can be `None` or missing for some symbols, the filter engine must treat that gracefully (see Task 3).

Do **not** change existing fields or their meaning.

---

## Task 3 — Extend the Central Filter Engine for Regimes

**Goal:** Teach the central filter engine in `stratdeck/tools/filters.py` to enforce the new regime constraints, and explain violations via `FilterDecision.reasons`.

### 3.1 Extend `evaluate_candidate_filters(...)`

In `stratdeck/tools/filters.py`, `evaluate_candidate_filters` currently handles numeric constraints using `StrategyFilters` and `DTERule`. For regime filters, we will pass it **both**:

- The existing `filters: StrategyFilters`
- The `StrategyTemplate` (or at least its regime fields) **or** a separate regime config object.

There are two acceptable patterns; choose one and implement consistently:

#### Option A — Pass full `StrategyTemplate`

Change the function signature to:

```python
def evaluate_candidate_filters(
    candidate: Mapping[str, Any],
    filters: Optional[StrategyFilters],
    dte_rule: Optional[DTERule] = None,
    strategy_template: Optional[StrategyTemplate] = None,
) -> FilterDecision:
    ...
```

Then:

- Use `filters` + `dte_rule` exactly as now (no behavioural changes).
- For regimes, read from `strategy_template.allowed_trend_regimes`, `strategy_template.allowed_vol_regimes`, etc.

#### Option B — Pass a small regime config

Define a small helper model or namedtuple that carries only the regime lists, and pass that instead of the full template. This is slightly more decoupled, but Option A is simpler if introducing new dependencies is acceptable.

> Choose Option A unless the existing codebase makes it awkward.

### 3.2 Implement regime checks

Within `evaluate_candidate_filters(...)`:

1. Extract candidate regimes:

   ```python
   trend_regime = candidate.get("trend_regime")
   vol_regime = candidate.get("vol_regime")
   ```

2. If `strategy_template` is provided, extract its regime constraints:

   ```python
   allowed_trend = strategy_template.allowed_trend_regimes or None
   allowed_vol = strategy_template.allowed_vol_regimes or None
   blocked_trend = strategy_template.blocked_trend_regimes or None
   blocked_vol = strategy_template.blocked_vol_regimes or None
   ```

3. Apply **allowed** rules:

   - If `allowed_trend` is not `None` and `trend_regime` is **not** in that list:
     - Append a reason such as:

       ```python
       reasons.append(
           f"trend_regime {trend_regime!r} not in allowed_trend_regimes {allowed_trend!r}"
       )
       ```

   - If `allowed_vol` is not `None` and `vol_regime` is **not** in that list:
     - Append a similar reason for volatility.

   **Missing data rule:**

   - If `allowed_trend` is set but `trend_regime` is `None` or missing, treat it as a **failure**, with a reason like:

     ```python
     reasons.append(
         "trend_regime is missing but allowed_trend_regimes is configured"
     )
     ```

   - Same for `allowed_vol`.

4. Apply **blocked** rules (if implemented):

   - If `blocked_trend` is set and `trend_regime` is in that list:
     - Append:

       ```python
       reasons.append(
           f"trend_regime {trend_regime!r} is in blocked_trend_regimes {blocked_trend!r}"
       )
       ```

   - Same for `blocked_vol`.

5. Ensure that all new regime checks feed into the same `reasons` list that ultimately determines `passed`:

   ```python
   passed = len(reasons) == 0
   return FilterDecision(passed=passed, applied=applied, reasons=reasons)
   ```

6. Do **not** modify how `applied` is used for numeric filters. If desired, you may add non-numeric hints (e.g. `applied["allowed_trend_regimes"]`), but this is optional.

### 3.3 Wire from `TradePlanner._evaluate_strategy_filters`

In `stratdeck/agents/trade_planner.py`, update `_evaluate_strategy_filters` to pass the strategy template through to the engine:

```python
def _evaluate_strategy_filters(
    self,
    candidate: Dict[str, Any],
    strategy: StrategyTemplate,
) -> FilterDecision:
    filters = getattr(strategy, "filters", None)
    dte_rule = getattr(strategy, "dte", None)
    return evaluate_candidate_filters(
        candidate,
        filters=filters,
        dte_rule=dte_rule,
        strategy_template=strategy,
    )
```

Adjust imports as needed.

---

## Task 4 — Tests for Regime-Aware Filtering

**Goal:** Verify that regime constraints behave as expected and integrate cleanly with the existing numeric filters.

### 4.1 Unit-level tests on the filter engine

Add tests to `tests/test_filters_engine.py` (or an equivalent test module that already covers `evaluate_candidate_filters`). If there isn’t one yet, create `tests/test_filters_engine.py` and include existing numeric tests plus new regime tests.

Add tests like:

1. **Allowed trend & vol pass**

   ```python
   def test_regime_filters_pass_when_in_allowed_lists():
       candidate = {
           "trend_regime": "uptrend",
           "vol_regime": "normal",
           "dte_target": 45,
           "pop": 0.60,
           "ivr": 0.30,
           "credit_per_width": 0.40,
       }

       filters = StrategyFilters(
           min_pop=0.55,
           min_ivr=0.20,
           min_credit_per_width=0.30,
       )

       template = StrategyTemplate(
           name="test_strategy",
           applies_to_universes=["index_core"],
           product_type="option",
           order_side="sell",
           option_type="put_spread",
           dte=DTERule(min=30, max=60),
           filters=filters,
           allowed_trend_regimes=["uptrend", "sideways"],
           allowed_vol_regimes=["normal", "high"],
       )

       decision = evaluate_candidate_filters(
           candidate,
           filters=filters,
           dte_rule=template.dte,
           strategy_template=template,
       )

       assert decision.passed is True
       assert decision.reasons == []
   ```

2. **Trend regime not allowed**

   ```python
   def test_regime_filters_fail_on_disallowed_trend():
       candidate = {
           "trend_regime": "downtrend",
           "vol_regime": "normal",
           "pop": 0.60,
           "ivr": 0.30,
           "credit_per_width": 0.40,
           "dte_target": 45,
       }

       filters = StrategyFilters(min_pop=0.55, min_ivr=0.20)

       template = StrategyTemplate(
           name="test_strategy",
           applies_to_universes=["index_core"],
           product_type="option",
           order_side="sell",
           option_type="put_spread",
           dte=DTERule(min=30, max=60),
           filters=filters,
           allowed_trend_regimes=["uptrend", "sideways"],
       )

       decision = evaluate_candidate_filters(
           candidate,
           filters=filters,
           dte_rule=template.dte,
           strategy_template=template,
       )

       assert decision.passed is False
       assert any("trend_regime" in r for r in decision.reasons)
   ```

3. **Missing trend when allowed list configured**

   ```python
   def test_regime_filters_fail_when_trend_missing_but_required():
       candidate = {
           "vol_regime": "normal",
           "pop": 0.60,
           "ivr": 0.30,
           "credit_per_width": 0.40,
           "dte_target": 45,
       }

       filters = StrategyFilters(min_pop=0.55)

       template = StrategyTemplate(
           name="test_strategy",
           applies_to_universes=["index_core"],
           product_type="option",
           order_side="sell",
           option_type="put_spread",
           dte=DTERule(min=30, max=60),
           filters=filters,
           allowed_trend_regimes=["uptrend", "sideways"],
       )

       decision = evaluate_candidate_filters(
           candidate,
           filters=filters,
           dte_rule=template.dte,
           strategy_template=template,
       )

       assert decision.passed is False
       assert any("trend_regime is missing" in r for r in decision.reasons)
   ```

4. **Vol regime not allowed**

   Similar to the trend tests, but for `allowed_vol_regimes` and `vol_regime`.

Make sure that:

- Numeric constraints are still applied as before.
- Regime reasons appear alongside numeric reasons if both fail.

### 4.2 Optional integration test via `TradePlanner`

Optionally, add a high-level test in `tests/test_trade_planner_regime_filters_integration.py` that:

- Creates a fake `StrategyTemplate` with `allowed_trend_regimes=["uptrend"]`.
- Constructs a fake candidate or task where `trend_regime="downtrend"`.
- Verifies that `TradePlanner._generate_for_task` discards the candidate (returns `None` or similar).

This ensures the wiring from `TradePlanner` through to `FilterDecision` works as expected.

---

## Task 5 — Debug / Observability

**Goal:** Leverage the existing filter-debug machinery to surface regime failures in logs for human inspection.

### 5.1 Confirm debug output includes regime fields

In `stratdeck/agents/trade_planner.py`, there is already a debug environment variable (e.g. `STRATDECK_DEBUG_FILTERS` or `STRATDECK_DEBUG_STRATEGY_FILTERS`) that logs candidate and filter decisions.

Ensure that the debug payload for filter decisions includes the new regime fields, for example:

```python
payload = {
    "symbol": candidate.get("symbol"),
    "strategy_type": candidate.get("strategy_type"),
    "dte_target": candidate.get("dte_target"),
    "ivr": candidate.get("ivr"),
    "pop": candidate.get("pop"),
    "credit_per_width": candidate.get("credit_per_width"),
    "trend_regime": candidate.get("trend_regime"),
    "vol_regime": candidate.get("vol_regime"),
    "accepted": decision.passed,
    "applied": decision.applied,
    "reasons": decision.reasons,
}
```

With debug enabled:

```bash
export STRATDECK_DEBUG_FILTERS=1
export STRATDECK_DATA_MODE=live

python -m stratdeck.cli trade-ideas   --universe index_core   --strategy short_put_spread_index_45d   --json-output /tmp/ideas_regime_debug.json
```

You should see log lines that clearly indicate when candidates are rejected due to trend/vol regime mismatches.

---

## Task 6 — Manual Sanity Check

**Goal:** Demonstrate that regime-aware filters actually change which strategies are suggested, in a way that matches human intuition.

Steps (for the human developer):

1. Configure regime filters in `strategies.yaml`, for example:

   ```yaml
   short_put_spread_index_45d:
     # ...
     allowed_trend_regimes: ["uptrend", "sideways"]
     allowed_vol_regimes: ["normal", "high"]
   ```

2. In a session where a symbol is in a **downtrend**, run:

   ```bash
   export STRATDECK_DATA_MODE=live
   export STRATDECK_DEBUG_FILTERS=1

   python -m stratdeck.cli trade-ideas      --universe index_core      --strategy short_put_spread_index_45d      --json-output /tmp/ideas_regime_test.json
   ```

3. Observe:

   - Candidates with `trend_regime="downtrend"` should be **rejected**.
   - Logs should include reasons like:
     - `trend_regime 'downtrend' not in allowed_trend_regimes ['uptrend', 'sideways']`

4. Confirm JSON output only contains strategies for symbols whose regimes satisfy the configuration.

This provides a human-level confirmation that regime filters are doing the right thing.

---

## Task 7 — Git Hygiene & PR

Before committing and pushing:

1. Run tests:

   ```bash
   pytest -q
   ```

2. Inspect the diff:

   ```bash
   git status
   git diff --stat
   git diff
   ```

   Ensure only the intended files are changed:

   - `stratdeck/strategies.py`
   - `stratdeck/tools/filters.py`
   - `stratdeck/agents/trade_planner.py`
   - `strategies.yaml`
   - `tests/test_filters_engine.py`
   - Any new integration test files

   Reset any generated noise (e.g. `.stratdeck/last_trade_ideas.json`).

3. Commit and push:

   ```bash
   git add stratdeck/strategies.py stratdeck/tools/filters.py stratdeck/agents/trade_planner.py
   git add strategies.yaml
   git add tests/test_filters_engine.py
   # plus any additional test files you added

   git commit -m "Add regime-aware strategy filters for trend and volatility"
   git push -u origin feature/regime-aware-filters
   ```

4. Open a PR from `feature/regime-aware-filters` → `main` on GitHub. Verify that CI is green and that there is a clear PR description of the change.

5. After merge:

   ```bash
   git checkout main
   git pull --ff-only origin main
   git branch -d feature/regime-aware-filters
   git push origin --delete feature/regime-aware-filters
   ```

---

## Constraints & Non-Goals (for Codex-Max)

While executing this task, **do not**:

- Change the semantics of existing numeric filters (IVR, POP, credit-per-width, DTE).
- Change the shape of `TradeIdea` JSON.
- Modify orchestrator, agents (beyond `TradePlanner`), or order placement logic.
- Introduce new external dependencies.
- Add network calls in tests.

Focus strictly on:

- Extending strategy models with optional regime filter fields.
- Ensuring candidates carry regime information.
- Extending the central filter engine to apply regime constraints and explain failures.
- Adding tests and debug visibility for regime-aware behaviour.
