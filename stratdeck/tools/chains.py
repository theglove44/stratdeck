import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from stratdeck.data.factory import get_provider
from stratdeck.tools.retries import call_with_retries

log = logging.getLogger(__name__)

_provider = None


def _p():
    global _provider
    if _provider is None:
        _provider = get_provider()
    return _provider


def set_provider(provider) -> None:
    """Test helper: override the lazily cached provider."""
    global _provider
    _provider = provider


def get_chain(symbol: str, expiry: Optional[str] = None) -> Dict[str, Any]:
    provider = _p()

    def _fetch():
        return provider.get_option_chain(symbol, expiry=expiry)

    try:
        return call_with_retries(
            _fetch,
            label=f"get_option_chain {symbol}",
            logger=log,
        ) or {}
    except Exception as exc:
        log.warning(
            "[chains] failed to fetch chain symbol=%s expiry=%s error=%r",
            symbol,
            expiry,
            exc,
        )
        return {}

def _nearest_expiry(days: int) -> str:
    return (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")


def _mock_chain(symbol: str, target_dte: int, n: int = 15) -> Dict[str, Any]:
    px = 500 if symbol.upper() == "SPX" else 50
    strikes = [round(px * (0.9 + i * 0.01), 2) for i in range(n)]
    expiry = _nearest_expiry(target_dte)
    puts = []
    calls = []
    for k, strike in enumerate(strikes):
        delta = min(0.05 + 0.03 * k, 0.45)
        bid = max(0.05, (0.40 - delta) * (5 if symbol.upper() == "SPX" else 1))
        ask = bid + (0.15 if symbol.upper() == "SPX" else 0.05)
        mid = round((bid + ask) / 2, 2)
        greeks = {"delta": round(delta, 2)}
        puts.append(
            {
                "type": "put",
                "strike": strike,
                "delta": greeks["delta"],
                "bid": round(bid, 2),
                "ask": round(ask, 2),
                "mid": mid,
                "greeks": greeks,
            }
        )
        calls.append(
            {
                "type": "call",
                "strike": strike,
                "delta": greeks["delta"],
                "bid": round(bid, 2),
                "ask": round(ask, 2),
                "mid": mid,
                "greeks": greeks,
            }
        )
    return {"symbol": symbol, "expiry": expiry, "puts": puts, "calls": calls}

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
