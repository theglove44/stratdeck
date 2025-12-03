# Codex-Max Task Spec — StratDeck Human Rules Output Fix & Verification

## 0. Meta

- **Repository**: `git@github.com:theglove44/stratdeck.git`
- **Branch**: Use existing feature branch for human rules (e.g. `feature/human-rules-strategy-engine`) or create one if needed.
- **Project**: StratDeck Agent System — options strategy scanner & lifecycle engine.
- **Primary user**: Chris.
- **Context**:
  - Human rules are already encoded in:
    - `stratdeck/config/strategies.yaml`
    - `stratdeck/agents/trade_planner.py`
    - `stratdeck/tools/chain_pricing_adapter.py`
    - `stratdeck/filters/human_rules.py`
    - `stratdeck/tools/position_monitor.py`
  - Tests currently pass: `pytest -q` → 96 passed.
- **Goal of this task**:
  - Fix missing / null output fields in `trade-ideas` JSON: `dte`, `short_legs[0].delta`, and any related metadata required to enforce / explain human rules.
  - Add robust **self-testing and self-correction** so Codex automatically re-runs tests and smoke checks and loops back to fix issues until all expectations are met.

---

## 1. Problems to Find and Fix

### 1.1 Known symptoms

These are observed from **live** `trade-ideas` runs:

1. **`dte` is `null` in JSON output**  
2. **`short_legs[0].delta` is `null`**  
3. Additional optional: ensure delta/dte correctly propagate in both verticals and iron condors.

### 1.2 What “fixed” means

- `dte` is a non-null integer derived from expiry.
- Short-leg delta is non-null and accurate.
- IC deltas (put + call) are non-null.
- Spread width remains 5.0 for human-rule strategies.
- No regressions introduced.

---

## 2. Files / Areas to Inspect

- `TradeIdea` model
- Candidate builder in planner
- Chain adapter (expiry & delta extraction)
- JSON serialization in CLI
- HumanRulesFilter dependencies
- Any intermediate transformations dropping fields

---

## 3. Required Behaviour

- Correct DTE calculation and propagation.
- Correct delta assignment and propagation.
- JSON ideas must reflect human rules fully.
- Planner should provide correct metadata to filters.

---

## 4. Implementation Requirements

- Add compute_dte() helper.
- Wire dte into candidates → TradeIdea → JSON.
- Wire delta from chain nodes into legs → JSON.
- Confirm IC legs both have delta.

---

## 5. Test & Self-Check Commands

Codex must run:

### Unit tests
```
pytest -q
```

### Mock-mode smoke
```
export STRATDECK_DATA_MODE=mock
python -m stratdeck.cli trade-ideas ...
```

Codex must inspect output and ensure:

- dte != null  
- short_legs[0].delta != null  
- IC both side deltas != null  
- spread_width == 5.0  

### Live-mode smoke
```
export STRATDECK_DATA_MODE=live
python -m stratdeck.cli trade-ideas ...
```

Codex must self-correct on any failure by:

- Inspecting code  
- Applying minimal patches  
- Re-running tests and smoke checks  
- Looping until everything passes or explaining a hard external blocker  

---

## 6. Self-Correction Loop

For every failure:

- Log failure, identify root cause  
- Fix code  
- Re-run failing test/smoke  
- Retry up to 3 times per failure mode  
- If unresolved: explain cause + next steps  

---

## 7. Deliverables Checklist

- [ ] pytest green  
- [ ] mock-mode trade-ideas JSON valid  
- [ ] live-mode trade-ideas JSON valid  
- [ ] dte and delta wired end-to-end  
- [ ] No regressions in width, structure  
