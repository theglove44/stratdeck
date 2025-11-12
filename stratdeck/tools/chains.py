from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from stratdeck.data.factory import get_provider

_provider = None

def _p():
    global _provider
    if _provider is None:
        _provider = get_provider()
    return _provider

def get_chain(symbol: str, expiry: Optional[str] = None) -> Dict[str, Any]:
    return _p().get_option_chain(symbol, expiry=expiry)

def _nearest_expiry(days: int) -> str:
    return (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")


def _mock_chain(symbol: str, target_dte: int, n: int = 15) -> Dict[str, Any]:
    px = 500 if symbol.upper() == "SPX" else 50
    strikes = [round(px * (0.9 + i * 0.01), 2) for i in range(n)]
    expiry = _nearest_expiry(target_dte)
    puts = []
    for k, strike in enumerate(strikes):
        delta = min(0.05 + 0.03 * k, 0.45)
        bid = max(0.05, (0.40 - delta) * (5 if symbol.upper() == "SPX" else 1))
        ask = bid + (0.15 if symbol.upper() == "SPX" else 0.05)
        puts.append({
            "type": "put",
            "strike": strike,
            "delta": round(delta, 2),
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "mid": round((bid + ask) / 2, 2),
        })
    return {"symbol": symbol, "expiry": expiry, "puts": puts}

def fetch_vertical_candidates(symbol: str, target_dte: int, target_delta: float, width: int) -> Dict[str, Any]:
    """
    Return a simple vertical candidate anchored at the short put closest to target_delta.
    Uses the configured data provider; falls back to a generated mock chain if empty.
    """
    expiry_hint = _nearest_expiry(target_dte)
    try:
        data = get_chain(symbol, expiry=expiry_hint) or {}
    except Exception as exc:
        print(f"[chains] warn: provider chain failed ({exc}); using mock data")
        data = _mock_chain(symbol, target_dte)
    puts = data.get("puts") or []
    if not puts:
        data = _mock_chain(symbol, target_dte)
        puts = data["puts"]

    puts = sorted(puts, key=lambda x: x.get("delta", 0))
    short = min(puts, key=lambda p: abs(float(p.get("delta", 0)) - float(target_delta)))
    long_strike = round(float(short.get("strike", 0)) - float(width), 2)
    long = min(puts, key=lambda p: abs(float(p.get("strike", 0)) - long_strike))
    candidate = {
        "symbol": symbol,
        "expiry": data.get("expiry", expiry_hint),
        "short": short,
        "long": long,
        "width": round(abs(float(short.get("strike", 0)) - float(long.get("strike", 0))), 2),
    }
    return candidate
