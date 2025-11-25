# IVR and /market-metrics

## Source of truth

The Tasty watchlist **IV Rank** column is backed by the `/market-metrics` field:

- `implied-volatility-index-rank` (with `implied-volatility-index-rank-source` typically `tos`).

StratDeck treats this as the canonical IV Rank and normalises it to a 0–1 float `ivr`.

## Pipeline

1. `fetch_market_metrics_raw(symbols)`  
   Thin wrapper over `/market-metrics` used for debugging and field inspection.

2. `_extract_ivr_from_item(item)`  
   - Prefer `implied-volatility-index-rank`.  
   - Fall back to other `*-implied-volatility-index-rank` fields if needed.  
   - Accepts both 0–1 and 0–100 inputs; 0–100 values are divided by 100.  
   - Clamps to `[0.0, 1.0]`.  

3. `fetch_iv_rank_for_symbols(symbols)`  
   Calls `/market-metrics`, runs `_extract_ivr_from_item` per symbol.

4. `build_iv_snapshot()`  
   Writes `stratdeck/data/iv_snapshot.json` as:

   ```json
   {
     "SPX": {"ivr": 0.26},
     "XSP": {"ivr": 0.27}
   }
   ```

## Consumers

- `scan_cache` reads the snapshot.
- `trade-ideas` / planner / agents read `ivr` from rows to enforce IV filters.

## Refreshing IVR

```bash
export STRATDECK_DATA_MODE=live

python -m stratdeck.cli refresh-ivr-snapshot
```

The refresh covers:

- Static universes from `strategies.yaml`.
- `tasty_watchlist_*` universes, such as `tasty_watchlist_chris_historical_trades`.

## Manual verification checklist

Set data mode to live:

```bash
export STRATDECK_DATA_MODE=live
```

Refresh the IV snapshot:

```bash
python -m stratdeck.cli refresh-ivr-snapshot
```

Run a scan for a universe/strategy that uses your Tasty watchlist:

```bash
python -m stratdeck.cli trade-ideas \
  --universe tasty_watchlist_chris_historical_trades \
  --strategy short_put_spread_equity_45d \
  --json-output > /tmp/ideas_watchlist_ivr.json
```

Inspect IVR in percent terms:

```bash
jq '.[] | {symbol, ivr_pct: (.ivr * 100)}' /tmp/ideas_watchlist_ivr.json
```

In Tasty, open the "Chris Historical Trades" watchlist and compare:

- Tasty IV Rank column.
- StratDeck `ivr_pct` for the same symbols.

Differences should be within about ±1 IV Rank point, allowing for live movement and UI rounding.

## PR description template

Use this when opening the PR:

```md
**Title:** Align IV Rank with Tasty UI and document IVR pipeline

### Summary

Align StratDeck's IV Rank (`ivr`) with the Tasty UI "IV Rank" column, and
document the IVR snapshot workflow for live trading universes.

### Changes

- Added a debug helper for `/market-metrics`:
  - `fetch_market_metrics_raw(symbols)` in `stratdeck/data/market_metrics.py`.
  - CLI command `dump-market-metrics` to pretty-print raw market-metrics JSON
    for a list of symbols.
- Updated IVR extraction logic:
  - Use `implied-volatility-index-rank` from `/market-metrics` as the
    canonical field backing Tasty's "IV Rank" column (TOS source).
- Added fallbacks to other `*-implied-volatility-index-rank` fields if the
    canonical field is missing.
- Normalise 0–100 inputs to 0–1, clamp to `[0.0, 1.0]`.
- Strengthened tests:
  - Coverage for canonical vs fallback field precedence.
  - Coverage for 0–1 vs 0–100 inputs and clamping.
  - Tests for the raw market-metrics helper using a fake session.
- Docs:
  - Added an IVR section to `README.md` describing the source field, 0–1
    normalisation, and how to run `refresh-ivr-snapshot`.
  - Added a dev-facing doc explaining the IVR pipeline and including a manual
    verification checklist.

### Verification

- `pytest -q` ✅
- `STRATDECK_DATA_MODE=live` and `refresh-ivr-snapshot` run successfully.
- `trade-ideas` IVR checks:
  - For `index_core` and `tasty_watchlist_chris_historical_trades` universes,
    `ivr * 100` matches the Tasty UI "IV Rank" column for symbols such as
    `SPX` and `XSP`, and selected watchlist names, within ~1 IV Rank point.
```
