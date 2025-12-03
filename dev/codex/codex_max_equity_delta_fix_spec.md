# Codex-Max Task Spec — Wire Live Tasty Delta into Equity Legs & Verify

## 0. Meta

- **Repository**: `git@github.com:theglove44/stratdeck.git`
- **Branch**: Use the existing human-rules feature branch (e.g. `feature/human-rules-strategy-engine`). If not checked out, create/update it from latest `main`.
- **Project**: StratDeck Agent System — options strategy scanner & lifecycle engine.
- **Primary user**: Chris.
- **Context**:
  - Human rules are encoded and working for DTE and spread width.
  - `TradeLeg` and `TradeIdea` now support `delta` and `dte`, and tests for mocks/index pass.
  - Live Tasty provider (`TastyProvider`) returns option chains as a dict with populated deltas.
  - Observed issue: In live trade-ideas output for equity universe, `short_legs[0].delta` is null despite chain having delta values.

## 1. Goal

Ensure equity strategies correctly wire real Tasty deltas into `TradeLeg.delta` and produce non-null deltas in live `trade-ideas` output. Add robust tests + self-correction.

## 2. Problems to Fix

- Live trade-ideas JSON shows `short_legs[0].delta = null`.
- Chain adapter must extract delta from live chain dict.
- Planner must preserve delta.
- Serialization must emit delta into JSON.

## 3. Areas Codex MUST Inspect

- `stratdeck/data/tasty_provider.py`
- `stratdeck/tools/chain_pricing_adapter.py`
- `stratdeck/agents/trade_planner.py`
- `TradeLeg` and `TradeIdea` models
- Tests: `tests/test_trade_idea_output_fields.py`

## 4. Required Post-Fix Behaviour

- Live equity trade-ideas must show:
  - `short_legs[0].delta` non-null.
  - `spread_width == 5.0`
  - `dte` correct
- ICs must show both deltas.

## 5. Implementation Requirements

### 5.1 Extract delta from chain dict

Live chain dict structure:

```
{
  "symbol": "AMZN",
  "expiry": "...",
  "puts": [
    {"strike": ..., "delta": -0.48, "greeks": {"delta": -0.48}, ...},
    ...
  ],
  "calls": [
    {"strike": ..., "delta": 0.51, "greeks": {"delta": 0.51}, ...},
    ...
  ]
}
```

Codex must:

- Identify the correct option entry for the selected strikes.
- Read `entry["delta"]` (or `entry["greeks"]["delta"]`).
- Assign to `TradeLeg.delta` in chain_pricing_adapter.

### 5.2 Preserve delta in planner + serialization

Codex must:

- Ensure planner copies `delta` into `TradeIdea.short_legs`.
- Ensure serialization (`model_dump`/`json`) includes delta.

## 6. Tests & Self-Check

### 6.1 Unit tests

- Run `pytest -q` until green.
- Add fixture-based test:
  - Mock TastyProvider.get_option_chain with a real-shaped chain dict.
  - Assert resulting TradeIdea.short_legs[].delta = chain delta.

### 6.2 Mock-mode smoke tests

Run:

```
export STRATDECK_DATA_MODE=mock
python -m stratdeck.cli trade-ideas --universe index_core --strategy short_put_spread_index_45d --max-per-symbol 1 --json-output
```

Check:

- delta != null
- dte correct
- width correct

### 6.3 Live-mode equity smoke tests

Run:

```
export STRATDECK_DATA_MODE=live
python -m stratdeck.cli trade-ideas --universe tasty_watchlist_chris_historical_trades --strategy short_put_spread_equity_45d --max-per-symbol 1 --json-output
```

Check:

- short_legs[0].delta != null

## 7. Self-Correction Loop

For each failure:

1. Log failing output.
2. Diagnose root cause.
3. Apply minimal fix.
4. Re-run failing command + `pytest -q`.
5. Up to 3 retries.
6. If still failing, document external blocker.

## 8. Deliverables

- pytest fully green.
- Mock-mode JSON valid.
- Live-mode JSON valid (non-null delta) or explained limitation.
- Fixture-based delta tests added.
- Equity delta fully wired.
