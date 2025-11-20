from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from stratdeck.tools.chains import get_chain

GREEK_KEYS = ("delta", "theta", "vega", "gamma")


def _coerce(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _leg_attr(leg: Any, name: str, default=None):
    if isinstance(leg, dict):
        return leg.get(name, default)
    return getattr(leg, name, default)


def _leg_side_multiplier(leg: Any) -> int:
    side = (_leg_attr(leg, "side", "") or _leg_attr(leg, "position", "") or "").lower()
    if side in {"short", "sell", "sell_to_open", "sell to open"}:
        return -1
    return 1


def _leg_qty(leg: Any) -> int:
    try:
        return abs(int(_leg_attr(leg, "qty", 1)))
    except Exception:
        return 1


def _nearest_option(options: Iterable[Dict[str, Any]], strike: float) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    best_diff = float("inf")
    for opt in options:
        try:
            diff = abs(float(opt.get("strike", 0.0)) - strike)
        except Exception:
            continue
        if diff < best_diff:
            best_diff = diff
            best = opt
    return best


def calc(symbol: str, expiry: str, legs: list[dict]):
    """
    Return the combined greeks for the spread:
      delta, theta, vega, gamma
    Uses live chain data when available; falls back to zeros if missing.
    """
    try:
        chain = get_chain(symbol, expiry=expiry) or {}
    except Exception:
        chain = {}

    totals: Dict[str, float] = {k: 0.0 for k in GREEK_KEYS}
    puts = chain.get("puts") or []
    calls = chain.get("calls") or []

    for leg in legs:
        strike = _coerce(_leg_attr(leg, "strike", 0.0))
        opt_type = (_leg_attr(leg, "type", "") or _leg_attr(leg, "option_type", "")).lower()
        options = calls if opt_type == "call" else puts
        quote = _nearest_option(options, strike)
        if not quote:
            continue
        multiplier = _leg_side_multiplier(leg) * _leg_qty(leg)
        greeks = quote.get("greeks") or {}
        for key in GREEK_KEYS:
            totals[key] += multiplier * _coerce(
                greeks.get(key, quote.get(key)),
                0.0,
            )

    return totals
