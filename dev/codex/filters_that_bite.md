## Filters That Bite

- Central filter engine lives in `stratdeck/tools/filters.py` with `evaluate_candidate_filters` returning a `FilterDecision`.
- Strategy config wiring: `StrategyTemplate.filters` → `StrategyFilters` (min/max POP, IVR, credit/width) plus optional `DTERule` → `evaluate_candidate_filters(...)`.
- `TradePlanner` delegates via `_evaluate_strategy_filters`, and attaches `filters_passed`, `filters_applied`, and `filter_reasons` on each `TradeIdea` without changing the JSON shape.

### Debugging

```bash
export STRATDECK_DATA_MODE=mock
export STRATDECK_DEBUG_STRATEGY_FILTERS=1  # or STRATDECK_DEBUG_FILTERS=1

python -m stratdeck.cli trade-ideas \
  --universe index_core \
  --strategy short_put_spread_index_45d \
  --json-output > /tmp/ideas.json
```

Example log payload:

```
[filters] {'symbol': 'SPX', 'strategy_type': 'short_put_spread', 'dte_target': 45, 'ivr': 0.18, 'pop': 0.6, 'credit_per_width': 0.2, 'accepted': False, 'applied': {'min_ivr': 0.2, 'min_pop': 0.55}, 'reasons': ['min_ivr 0.18 < 0.20']}
```
