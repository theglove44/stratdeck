# Fix XSP Index Strike Scaling

## What was happening
- XSP trade ideas were emitted with strikes ~10× above the XSP underlying (e.g., underlying ≈ 660.3, strikes ≈ 6,829.62 / 6,824.62) while SPX ideas were sane (underlying ≈ 6,607.3, strikes ≈ 6,574.32 / 6,569.32).
- Root cause: the TA→leg builder fell back to the last support/resistance level even when no level existed below/above the underlying. When XSP reused SPX-scale TA levels, that fallback injected SPX-sized strikes into XSP spreads.

## Before (reported)
```json
{
  "symbol": "XSP",
  "underlying_price_hint": 660.3,
  "legs": [{"strike": 6829.62}, {"strike": 6824.62}]
}
{
  "symbol": "SPX",
  "underlying_price_hint": 6607.3,
  "legs": [{"strike": 6574.32}, {"strike": 6569.32}]
}
```

## After (local mock sanity)
```json
{
  "symbol": "XSP",
  "underlying_price_hint": 110.7965,
  "legs": [{"strike": 110.7037}, {"strike": 105.7037}]
}
{
  "symbol": "SPX",
  "underlying_price_hint": 102.1651,
  "legs": [{"strike": 102.1592}, {"strike": 97.1592}]
}
```

## Notes
- New guardrails in `TradePlanner._build_legs_from_ta` stop using out-of-scale TA levels when none are on the correct side of price; spreads fall back to the underlying-based strike math instead.
- Regression tests (`tests/test_xsp_strike_scaling.py`) fail on the 10×-strike bug and now lock the corrected behavior.
- Live-mode reproduction wasn’t run here (network-restricted environment); mock run confirms strikes now stay near the underlying.
