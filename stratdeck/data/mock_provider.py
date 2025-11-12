# stratdeck/data/mock_provider.py
from typing import Dict, Any, List, Optional
from .provider import IDataProvider
from datetime import datetime, timedelta

class MockProvider(IDataProvider):
    def get_quote(self, symbol: str) -> Dict[str, Any]:
        return {"symbol": symbol, "last": 123.45}

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> Dict[str, Any]:
        # Generate a synthetic chain near ATM with monotonic deltas
        px = 500 if symbol.upper() == "SPX" else 50
        strikes = [round(px * (0.9 + i * 0.01), 2) for i in range(15)]
        def day_str(d: int) -> str:
            return (datetime.utcnow() + timedelta(days=d)).strftime("%Y-%m-%d")
        expiry = expiry or day_str(30)
        puts: List[Dict[str, Any]] = []
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

    def get_account_summary(self) -> Dict[str, Any]:
        return {"buying_power": 100000.0, "cash": 50000.0, "equity": 150000.0}

    def get_positions(self) -> List[Dict[str, Any]]:
        return []

    def preview_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": True, "fill_price": 1.23, "fees": 1.00, "preview": True}

    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": True, "order_id": "MOCK-ORDER-1"}
