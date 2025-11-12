# stratdeck/tools/pricing.py
from typing import Dict, Any
from stratdeck.data.factory import get_provider

_provider = None

def _p():
    global _provider
    if _provider is None:
        _provider = get_provider()
    return _provider

def last_price(symbol: str) -> float:
    q: Dict[str, Any] = _p().get_quote(symbol)
    return float(q["last"])


def credit_for_vertical(vert: Dict) -> float:
    """
    Mid-price credit for short put spread: credit = short_mid - long_mid.
    """
    short_mid = float(vert["short"]["mid"])
    long_mid  = float(vert["long"]["mid"])
    credit = round(max(short_mid - long_mid, 0.01), 2)
    return credit

def pop_estimate(vert: Dict, target_delta: float) -> float:
    """
    POP heuristic = 1 - short_delta with a tiny cushion if width is generous.
    """
    sd = float(vert["short"]["delta"])
    base = max(0.50, min(0.95, 1.0 - sd))
    width = float(vert["width"])
    bonus = min(width * 0.002, 0.02)  # small bump for wider spreads, up to +2%
    return round(base + bonus, 2)