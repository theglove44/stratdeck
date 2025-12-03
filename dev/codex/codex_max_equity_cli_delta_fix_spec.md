# Codex-Max Task Spec — Fix Equity CLI Path Deltas (tasty_watchlist_chris_historical_trades)

## 0. Meta

- **Repo**: `git@github.com:theglove44/stratdeck.git`
- **Branch**: Continue on the human-rules feature branch already used for delta work.
- **Primary target**: CLI path:
  ```
  STRATDECK_DATA_MODE=live python -m stratdeck.cli trade-ideas     --universe tasty_watchlist_chris_historical_trades     --strategy short_put_spread_equity_45d     --max-per-symbol 1     --json-output
  ```
- **Observed bug**: All equity ideas show `short_legs[0].delta = null` despite live chain dict containing deltas.

## 1. Discovery Path Requirements
Codex must trace the exact call path used for the equity strategy + tasty watchlist universe:
- Universe loader
- TradePlanner path
- ChainPricingAdapter path for equity
- TastyProvider.get_option_chain()
- Leg building logic
- TradeIdea serialization

Identify the exact function(s) used to build equity verticals.

## 2. Implementation Requirements
### 2.1 Wire delta from chain dict
Live chain format:
```
{
  "symbol": "AMZN",
  "expiry": "...",
  "puts": [
    {"strike": ..., "delta": -0.48, "greeks": {"delta": -0.48}, ...},
    ...
  ],
  "calls": [...]
}
```
Codex must:
- Map the selected short put leg (via strike/expiry) to the correct dict entry.
- Assign delta = entry["delta"] into TradeLeg.delta.
- Apply same for short call leg in ICs.

### 2.2 Planner and Serialization
Codex must ensure:
- TradePlanner preserves leg.delta.
- TradeIdea.short_legs[] includes delta.
- JSON output includes delta (model_dump must not drop it).

## 3. Required Tests
### 3.1 Unit tests
- Add a fixture-based live-like chain dict.
- Mock provider.get_option_chain to return this dict.
- Generate TradeIdea via planner or CLI.
- Assert TradeIdea.short_legs[0].delta == fixture delta.

### 3.2 CLI-level mock test
- Invoke the exact CLI path with `STRATDECK_DATA_MODE=mock`.
- Patch provider to return equity chain fixture.
- Assert:
  - dte != null
  - spread_width == 5.0
  - short_legs[0].delta != null

### 3.3 Mock-mode smoke
```
export STRATDECK_DATA_MODE=mock
python -m stratdeck.cli trade-ideas ...
```
Verify delta exists.

### 3.4 Live-mode best-effort
If Codex environment can't fetch live Tasty data, document the limitation.

## 4. Self-Correction Loop
For every failure:
1. Capture failing command + output.
2. Diagnose root cause.
3. Apply minimal fix.
4. Re-run the failing command + pytest.
5. Retry up to 3 iterations.
6. If still failing, document external constraints.

## 5. Done Criteria
- pytest passes.
- CLI mock test asserts delta present.
- CLI mock-mode output: dte valid, width valid, delta non-null.
- Equity chain adapter identical in correctness to index path.
- Limitations documented if live data unavailable.
