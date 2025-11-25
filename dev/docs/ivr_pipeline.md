# IVR pipeline

- Source: tastytrade `/market-metrics` using the canonical `tw-implied-volatility-index-rank` field (matches the Tasty watchlist "IV Rank" column). `_extract_ivr_from_item` normalises values to 0–1, divides 0–100 ranges by 100, and clamps out-of-band numbers. Fallback order after the canonical field: `implied-volatility-index-rank`, `tos-implied-volatility-index-rank`, then any other `implied-volatility-index-rank*` key that is not a source tag.
- Storage: `stratdeck/tools/build_iv_snapshot.py` writes `stratdeck/data/iv_snapshot.json` with shape `{SYMBOL: {"ivr": float_0_to_1}}`. The symbol list comes from `get_live_universe_symbols()` (DXLink + tasty watchlists).
- Consumers: `tools/vol.load_snapshot()` returns a flat `{symbol: ivr}` map; `tools/scan_cache.attach_ivr_to_scan_rows()` enriches scan rows before trade-planner/agents read them.
- Refresh cadence: run `STRATDECK_DATA_MODE=live python -m stratdeck.cli refresh-ivr-snapshot` once per trading day (or whenever you want a fresh IVR snapshot). Missing symbols stay absent in the snapshot; downstream filters treat them as neutral.
- Debugging: `python -m stratdeck.cli dump-market-metrics --symbols SPX,GLD,AMD` pretty-prints the raw `/market-metrics` payload so you can compare the canonical field against the Tasty UI IV Rank column.

## Manual verification checklist

- Run `python -m stratdeck.cli dump-market-metrics --symbols ETHA,GLD,AMD,SPY,SPX` in live mode and note the `tw-implied-volatility-index-rank` values.
- Compare those values (scaled 0–100) with the Tasty watchlist IV Rank column for the same timestamp; tolerance should be within about one point.
- Run `python -m stratdeck.cli trade-ideas --json-output` and confirm the `ivr` in the output (scaled to 0–100) matches the canonical field above for the sampled symbols.
