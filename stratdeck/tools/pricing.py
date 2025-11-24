# stratdeck/tools/pricing.py
from typing import Any, Dict

from stratdeck.data.factory import get_provider

_provider = None


def _p():
    global _provider
    if _provider is None:
        _provider = get_provider()
    return _provider


def last_price(symbol: str) -> float:
    """
    Return a best-effort underlying price using mid/mark before falling back to last.
    """
    q: Dict[str, Any] = _p().get_quote(symbol) or {}
    for key in ("mark", "mid", "last"):
        val = q.get(key)
        try:
            if val is not None:
                return float(val)
        except Exception:
            continue
    # Last resort: average bid/ask if provided
    bid = q.get("bid")
    ask = q.get("ask")
    try:
        if bid is not None and ask is not None:
            return (float(bid) + float(ask)) / 2.0
    except Exception:
        pass
    return float(q.get("last") or 0.0)


def credit_for_vertical(vert: Dict) -> float:
    """
    Mid-price credit for short put spread: credit = short_mid - long_mid.
    """
    short_mid = float(vert["short"]["mid"])
    long_mid  = float(vert["long"]["mid"])
    credit = round(max(short_mid - long_mid, 0.01), 2)
    return credit

def vertical_credit(vert: Dict) -> float:
    """Legacy alias expected by chain_pricing_adapter."""
    return credit_for_vertical(vert)

def pop_estimate(vert: Dict, target_delta: float | None = None) -> float:
    """
    POP heuristic.

    Primary intent:
      - Use the actual short-leg delta from the chain if we have it.
      - Otherwise fall back to the strategy-configured target delta.
      - As a last resort, assume a 0.20 delta.

    Then:
      POP ≈ 1 - short_delta, with a small bump for wider spreads.
    """
    # 1) Try the chain delta first.
    sd_raw = vert.get("short", {}).get("delta", 0.0)
    sd: float | None
    try:
        sd = float(sd_raw)
    except Exception:
        sd = None

    # 2) If chain delta is missing/zero-ish, use target_delta if provided.
    if sd is None or sd <= 0.0:
        if target_delta is not None:
            try:
                sd = abs(float(target_delta))
            except Exception:
                sd = None

    # 3) Absolute safety net.
    if sd is None or sd <= 0.0:
        sd = 0.20

    # Core heuristic: 1 - delta, clipped into [0.50, 0.95].
    base = max(0.50, min(0.95, 1.0 - abs(sd)))

    # Small bump for wider spreads, up to +2%.
    width_raw = vert.get("width", 0.0)
    try:
        width = float(width_raw)
    except Exception:
        width = 0.0
    bonus = min(width * 0.002, 0.02)

    return round(base + bonus, 2)
