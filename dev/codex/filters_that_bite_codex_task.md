# Codex-Max Task — Filters That Bite & Explain Themselves

## Repo & Branch

- **Repo:** `git@github.com:theglove44/stratdeck.git`
- **Default branch:** `main`
- **Feature branch for this task:** `feature/filters-that-bite`

You are working in the `stratdeck` Python project. The goal of this task is to centralise and harden the trade-idea filtering logic so that:

1. Filters actually **bite** (properly gate candidates using strategy constraints like IVR, POP, credit-per-width, DTE).
2. Filters **explain themselves** via a structured decision object attached to each `TradeIdea`.
3. There is a **single, testable filter engine** that all trade-idea generation flows use.

No network calls are allowed in tests. Use fakes/mocks/synthetic data only.

---

## Context (What Already Exists)

### TradePlanner / Trade Ideas

- The main trade-idea generation logic lives in:

  - `stratdeck/agents/trade_planner.py`

- The relevant flow:

  - `_generate_for_task(...)` builds a `candidate: Dict[str, Any]` with keys like:

    - `symbol`
    - `strategy_type`
    - `direction`
    - `spread_width`
    - `dte_target`
    - `pop`
    - `ivr`
    - `credit_per_width`
    - `estimated_credit`

  - It currently calls a helper:

    ```python
    decision = self._evaluate_strategy_filters(candidate, task.strategy)
    ```

    If `decision.passed` is `False`, the candidate is dropped.

  - A `TradeIdea` is created with:

    ```python
    idea = TradeIdea(
        ...
        ivr=ivr,
        pop=pop,
        credit_per_width=credit_per_width,
        estimated_credit=estimated_credit,
        strategy_id=template_name,
        universe_id=universe_name,
        filters_passed=decision.passed,
        filters_applied=decision.applied or {},
        filter_reasons=decision.reasons or [],
    )
    ```

- There is a `FilterDecision` class defined in `trade_planner.py`:

  ```python
  @dataclass
  class FilterDecision:
      passed: bool
      applied: Dict[str, float]
      reasons: List[str]
  ```

- `TradePlanner._evaluate_strategy_filters(...)` currently:

  - Reads `strategy.filters` (a `StrategyFilters` instance) if present.
  - Applies **minimum** thresholds for:

    - `min_pop`
    - `min_ivr`
    - `min_credit_per_width`

  - It is *tolerant of missing IVR*:
    - If `ivr` is `None`, the `min_ivr` check is skipped, which means IVR does **not** bite.

  - It logs decisions when `STRATDECK_DEBUG_STRATEGY_FILTERS=1`.

### Strategy Config

- Strategy configuration models live in:

  - `stratdeck/strategies.py`

- `StrategyFilters` is defined as:

  ```python
  class StrategyFilters(BaseModel):
      """
      Optional filters that a candidate TradeIdea must satisfy.
      All are expressed as fractions, not percents (0.50 = 50%).
      """

      min_pop: Optional[float] = None
      max_pop: Optional[float] = None
      min_credit_per_width: Optional[float] = None
      min_ivr: Optional[float] = None
      max_ivr: Optional[float] = None
  ```

- `StrategyTemplate` contains:

  - `name`, `label`, `enabled`
  - `applies_to_universes: List[str]`
  - `product_type`, `order_side`, `option_type`
  - `dte: Optional[DTERule]`
  - `filters: Optional[StrategyFilters]`
  - plus other fields (delta band, width rules, etc.).

- `DTERule` is:

  ```python
  class DTERule(BaseModel):
      """
      Target days-to-expiry band for a strategy.
      """

      target: Optional[int] = None
      min: Optional[int] = None
      max: Optional[int] = None
  ```

  This is used by `TradePlanner` to pick a target DTE via `choose_target_dte(...)`.

### Debug / Logging

- There is an environment variable `STRATDECK_DEBUG_STRATEGY_FILTERS` that enables filter debug logging.
- `_generate_for_task` logs candidate details and filter outcomes when this debug flag is enabled.

---

## High-Level Requirements

1. Introduce a **central filter engine** (pure function) that evaluates a `candidate` against a `StrategyFilters` instance (and an optional `DTERule`) and returns a `FilterDecision`.
2. Make this engine the **single source of truth** for candidate pass/fail decisions used in TradePlanner.
3. Tighten semantics so filters **properly gate** candidates:
   - `min_ivr`, `max_ivr`, `min_pop`, `max_pop`, `min_credit_per_width`, and DTE constraints must be enforced.
   - Missing required data (e.g. IVR when `min_ivr` is set) should **fail** with a clear reason, not be silently skipped.
4. Preserve the `TradeIdea` JSON shape and the existing fields:
   - `filters_passed`, `filters_applied`, `filter_reasons` must remain.
5. Provide clear, structured debug logging for both accepted and rejected candidates controlled by an env var.
6. Add unit tests for the filter engine and a light integration test through `TradePlanner._evaluate_strategy_filters(...)`.

Non-goals for this task:

- No changes to how IVR is fetched or stored (`iv_snapshot.json` pipeline is out-of-scope).
- No changes to orchestrator / agents / order placement logic.
- No new strategy types or universes.
- No new data providers or real network calls in tests.
- No major redesign of direction / trend / volatility regime logic (that can be a later slice).

---

## Implementation Plan

### Task 1 — Create a Central Filter Engine Module

**Goal:** Move the filter logic out of `TradePlanner` into a dedicated, pure module under `stratdeck/tools`.

1. Create a new module:

   - `stratdeck/tools/filters.py`

2. In this module, add:

   ```python
   # stratdeck/tools/filters.py
   from __future__ import annotations

   from dataclasses import dataclass
   from typing import Any, Dict, List, Mapping, Optional

   from ..strategies import StrategyFilters, DTERule


   @dataclass
   class FilterDecision:
       passed: bool
       applied: Dict[str, float]
       reasons: List[str]
   ```

3. Implement a pure filter evaluation function:

   ```python
   def evaluate_candidate_filters(
       candidate: Mapping[str, Any],
       filters: Optional[StrategyFilters],
       dte_rule: Optional[DTERule] = None,
   ) -> FilterDecision:
       ...
   ```

4. Behaviour for `evaluate_candidate_filters`:

   - If `filters` is `None`, return:

     ```python
     FilterDecision(passed=True, applied={}, reasons=[])
     ```

   - Extract from `candidate` (using `.get`):

     - `pop`
     - `ivr`
     - `credit_per_width`
     - `dte_target` (or `dte`, depending on what the candidate uses — the current code uses `dte_target`).

   - Extract from `filters`:

     - `min_pop`, `max_pop`
     - `min_ivr`, `max_ivr`
     - `min_credit_per_width`

   - For each configured constraint (non-`None`):

     - Add it to `applied` as a float.
     - Evaluate the condition and append a human-readable string to `reasons` **if it fails**.

   - **Important: data-missing semantics must be strict:**

     - If a filter is configured but the metric is `None` (or not present), treat this as a **failure**, not a skip, with a reason like:

       - `"min_ivr check failed: ivr is missing"`
       - `"min_pop check failed: pop is missing"`
       - `"min_credit_per_width check failed: credit_per_width is missing"`

   - Example checks:

     ```python
     if filters.min_pop is not None:
         applied["min_pop"] = float(filters.min_pop)
         if pop is None:
             reasons.append("min_pop check failed: pop is missing")
         elif pop < filters.min_pop:
             reasons.append(f"min_pop {float(pop):.2f} < {float(filters.min_pop):.2f}")

     if filters.max_pop is not None:
         applied["max_pop"] = float(filters.max_pop)
         if pop is None:
             reasons.append("max_pop check failed: pop is missing")
         elif pop > filters.max_pop:
             reasons.append(f"max_pop {float(pop):.2f} > {float(filters.max_pop):.2f}")
     ```

     ```python
     if filters.min_ivr is not None:
         applied["min_ivr"] = float(filters.min_ivr)
         if ivr is None:
             reasons.append("min_ivr check failed: ivr is missing")
         elif ivr < filters.min_ivr:
             reasons.append(f"min_ivr {float(ivr):.2f} < {float(filters.min_ivr):.2f}")

     if filters.max_ivr is not None:
         applied["max_ivr"] = float(filters.max_ivr)
         if ivr is None:
             reasons.append("max_ivr check failed: ivr is missing")
         elif ivr > filters.max_ivr:
             reasons.append(f"max_ivr {float(ivr):.2f} > {float(filters.max_ivr):.2f}")
     ```

     ```python
     if filters.min_credit_per_width is not None:
         applied["min_credit_per_width"] = float(filters.min_credit_per_width)
         if credit_per_width is None:
             reasons.append(
                 "min_credit_per_width check failed: credit_per_width is missing"
             )
         elif credit_per_width < filters.min_credit_per_width:
             reasons.append(
                 "min_credit_per_width "
                 f"{float(credit_per_width):.3f} < {float(filters.min_credit_per_width):.3f}"
             )
     ```

   - DTE band enforcement using `dte_rule`:

     - If `dte_rule` is not `None` and `candidate` has `dte_target` (or whatever key the planner uses):

       ```python
       dte = candidate.get("dte_target")
       if dte is not None:
           if dte_rule.min is not None:
               applied["dte_min"] = float(dte_rule.min)
               if dte < dte_rule.min:
                   reasons.append(f"dte {dte} < dte_min {dte_rule.min}")
           if dte_rule.max is not None:
               applied["dte_max"] = float(dte_rule.max)
               if dte > dte_rule.max:
                   reasons.append(f"dte {dte} > dte_max {dte_rule.max}")
       ```

   - At the end:

     ```python
     passed = len(reasons) == 0
     return FilterDecision(passed=passed, applied=applied, reasons=reasons)
     ```

5. Do **not** import or depend on `TradePlanner` here. This module must be pure and reusable.

---

### Task 2 — Refactor TradePlanner to Use the New Engine

**Goal:** Make `TradePlanner` delegate to the new filter engine, and remove the old inline implementation.

1. In `stratdeck/agents/trade_planner.py`:

   - Remove the existing `FilterDecision` class definition.
   - Import `FilterDecision` and `evaluate_candidate_filters` from the new module:

     ```python
     from ..tools.filters import FilterDecision, evaluate_candidate_filters
     ```

2. Replace the body of `_evaluate_strategy_filters` with a thin adapter:

   ```python
   def _evaluate_strategy_filters(
       self,
       candidate: Dict[str, Any],
       strategy: Any,
   ) -> FilterDecision:
       filters = getattr(strategy, "filters", None)
       dte_rule = getattr(strategy, "dte", None)
       return evaluate_candidate_filters(candidate, filters, dte_rule)
   ```

3. Keep the call-site in `_generate_for_task` the same:

   ```python
   decision = self._evaluate_strategy_filters(candidate, task.strategy)
   ```

4. Ensure that `TradeIdea` construction still passes:

   - `filters_passed=decision.passed`
   - `filters_applied=decision.applied or {}`
   - `filter_reasons=decision.reasons or []`

   No shape changes to the `TradeIdea` model or its JSON output.

---

### Task 3 — Improve Debug / Introspection for Filters

**Goal:** Make it easy to see why candidates passed or failed filters in logs, without changing normal JSON output.

1. In `stratdeck/agents/trade_planner.py`, keep the existing debug env var but make it slightly more flexible:

   ```python
   DEBUG_FILTERS = (
       os.getenv("STRATDECK_DEBUG_STRATEGY_FILTERS") == "1"
       or os.getenv("STRATDECK_DEBUG_FILTERS") == "1"
   )
   ```

2. In `_generate_for_task`, before calling `_evaluate_strategy_filters`, keep or standardise the candidate logging:

   ```python
   if DEBUG_FILTERS:
       print("[trade-ideas] candidate before filters:", candidate, file=sys.stderr)
   ```

3. After the decision is computed, add or standardise a structured debug log call:

   ```python
   if DEBUG_FILTERS:
       status = "PASSED" if decision.passed else "FAILED"
       detail = "; ".join(decision.reasons) if decision.reasons else "ok"
       log.debug(
           "[filters] %s %s %s %s",
           symbol,
           strategy_type,
           status,
           detail,
       )
   ```

4. Optionally, create a small helper function in `trade_planner.py` for logging filter decisions:

   ```python
   def _log_filter_decision(
       candidate: Dict[str, Any],
       decision: FilterDecision,
   ) -> None:
       if not DEBUG_FILTERS:
           return
       payload = {
           "symbol": candidate.get("symbol"),
           "strategy_type": candidate.get("strategy_type"),
           "dte_target": candidate.get("dte_target"),
           "ivr": candidate.get("ivr"),
           "pop": candidate.get("pop"),
           "credit_per_width": candidate.get("credit_per_width"),
           "accepted": decision.passed,
           "applied": decision.applied,
           "reasons": decision.reasons,
       }
       log.debug("[filters] %s", payload)
   ```

   And call it instead of hand-building the log message.

5. Do **not** modify `TradeIdea` JSON or CLI output. Debug should be strictly via logs / stderr.

---

### Task 4 — Unit Tests for the Filter Engine

**Goal:** Lock in behaviour of `evaluate_candidate_filters` with synthetic data and no network calls.

1. Add a new test module, e.g.:

   - `tests/test_filters_engine.py`

2. Cover at least the following scenarios:

   #### 4.1 All constraints satisfied

   - Candidate:

     ```python
     candidate = {
         "pop": 0.60,
         "ivr": 0.35,
         "credit_per_width": 0.40,
         "dte_target": 45,
     }
     ```

   - Filters:

     ```python
     filters = StrategyFilters(
         min_pop=0.55,
         max_pop=0.95,
         min_ivr=0.20,
         max_ivr=0.90,
         min_credit_per_width=0.30,
     )
     ```

   - DTE rule:

     ```python
     dte_rule = DTERule(min=30, max=60)
     ```

   - Expect:

     - `decision.passed is True`
     - `decision.reasons == []`
     - `decision.applied` contains keys `min_pop`, `max_pop`, `min_ivr`, `max_ivr`, `min_credit_per_width`, `dte_min`, `dte_max`.

   #### 4.2 IVR too low

   - `candidate["ivr"] = 0.18`, `filters.min_ivr = 0.20`.

   - Expect:

     - `passed is False`
     - `reasons` contains `"min_ivr 0.18 < 0.20"` (format accordingly; use `in` assertions to ignore float rounding).

   #### 4.3 POP too low

   - `candidate["pop"] = 0.52`, `filters.min_pop = 0.55`.

   - Expect:

     - `passed is False`
     - Reason indicates `min_pop` failure.

   #### 4.4 POP too high (max_pop)

   - `candidate["pop"] = 0.82`, `filters.max_pop = 0.70`.

   - Expect a `max_pop` failure.

   #### 4.5 Credit per width too low

   - `candidate["credit_per_width"] = 0.18`, `filters.min_credit_per_width = 0.20`.

   - Expect a `min_credit_per_width` failure.

   #### 4.6 Missing IVR with min_ivr configured

   - `candidate["ivr"] = None` (or no `ivr` key), `filters.min_ivr = 0.20`.

   - Expect:

     - `passed is False`
     - Reason: `"min_ivr check failed: ivr is missing"`.

   #### 4.7 DTE outside allowed band

   - `candidate["dte_target"] = 60`, `dte_rule.min = 30`, `dte_rule.max = 50`.

   - Expect:

     - `passed is False`
     - Reason indicates `"dte 60 > dte_max 50"`.

   #### 4.8 No filters configured

   - `filters = None` or `StrategyFilters()` with all `None`.

   - Expect:

     - `passed is True`
     - `applied == {}`
     - `reasons == []`.

3. Tests must not call real providers or perform network I/O. Only construct `StrategyFilters`, `DTERule`, and dict candidates directly.

---

### Task 5 — Light Integration Test for TradePlanner

**Goal:** Verify that `TradePlanner._evaluate_strategy_filters` correctly delegates to `evaluate_candidate_filters` and that filter outcomes propagate into `TradeIdea`.

1. Add a test module such as:

   - `tests/test_trade_planner_filters_integration.py`

2. Use a minimal setup:

   - Construct a simple `StrategyTemplate` with:

     - `filters=StrategyFilters(min_ivr=0.20, min_pop=0.55)`
     - A simple `DTERule` (for the integration test you can keep DTE rules simple).

   - Instantiate a `TradePlanner` with a fake provider / chain adapter so it doesn’t hit the network.

3. Design two small integration cases (can be fairly high-level):

   - **Case A (Pass):** Candidate with ivr/pop/credit_per_width clearly above thresholds.
   - **Case B (Fail):** Candidate with low IVR or POP so that filters fail.

4. Assert for Case A:

   - A `TradeIdea` is produced.
   - `filters_passed is True`.
   - `filter_reasons` is empty.

5. Assert for Case B:

   - No `TradeIdea` is produced (the candidate is dropped) **or** the planner returns `None` for that symbol/task.
   - If you can access the internal decision, ensure `filters_passed is False`.

---

### Task 6 — Minimal Dev Documentation

**Goal:** Document where filters live and how to debug them.

1. Add a short markdown file:

   - `dev/codex/filters_that_bite.md`

2. Include:

   - A brief description of the filter engine and its location:

     - `stratdeck/tools/filters.py`.

   - The mapping between strategy config and filter engine:

     - `StrategyTemplate.filters` → `StrategyFilters` → `evaluate_candidate_filters(...)`.

   - Pointer to `TradePlanner` and how it uses the engine.

   - How to debug:

     ```bash
     export STRATDECK_DEBUG_STRATEGY_FILTERS=1
     # or:
     export STRATDECK_DEBUG_FILTERS=1

     python -m stratdeck.cli trade-ideas        --universe index_core        --strategy short_put_spread_index_45d        --json-output > /tmp/ideas.json
     ```

   - Example of a debug log line showing:

     - Symbol
     - Strategy
     - Accepted/rejected
     - Reasons

---

## Sanity Checks Before Finishing

Before considering the task done, make sure:

1. **Code compiles / imports cleanly**:

   - `pytest -q` passes.
   - No circular imports introduced between `filters.py` and `trade_planner.py`.

2. **CLI still works for basic scenarios**:

   - For example:

     ```bash
     export STRATDECK_DATA_MODE=live  # or "mock" depending on your environment

     python -m stratdeck.cli trade-ideas        --universe index_core        --strategy short_put_spread_index_45d        --json-output > /tmp/ideas_filters.json
     ```

   - Confirm the JSON still includes:

     - `filters_passed`
     - `filters_applied`
     - `filter_reasons`

3. **Filters actually bite**:

   - In debug mode, observe at least one candidate being rejected because:

     - IVR too low.
     - POP too low.
     - Credit-per-width too low.
     - DTE outside the configured band.
     - Or missing required metrics when filters are configured.

4. **Explanations are clear**:

   - `filter_reasons` and log messages should be understandable without reading the code, e.g.:

     - `"min_ivr 0.18 < 0.20"`
     - `"min_credit_per_width 0.180 < 0.200"`
     - `"dte 60 > dte_max 50"`
     - `"min_ivr check failed: ivr is missing"`

---

## What Not to Change

While completing this task, **do not**:

- Change how IVR is fetched, normalised, or stored (`iv_snapshot.json` behaviour is out-of-scope).
- Alter the public JSON shape of `TradeIdea` objects.
- Modify orchestrator/agent logic or order placement.
- Introduce new external dependencies or new data providers.
- Introduce network calls into tests.

Focus strictly on:

- The central filter engine.
- Wiring it into `TradePlanner`.
- Debug/introspection hooks.
- Tests and minimal documentation.
