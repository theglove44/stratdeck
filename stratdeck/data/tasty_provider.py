# stratdeck/data/tasty_provider.py
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from .provider import IDataProvider

try:  # suppress noisy TLS warning on macOS + Python 3.9
    import warnings

    warnings.filterwarnings("ignore", category=UserWarning, module="urllib3")
    import urllib3
    urllib3.disable_warnings()
except Exception:
    pass


class TastyProvider(IDataProvider):
    """Minimal REST client for the tastytrade API."""

    API_BASE = os.getenv("TASTY_API_URL", "https://api.tastyworks.com")
    INDEX_SYMBOLS = {"SPX", "RUT", "NDX", "VIX", "XSP"}
    MAX_OPTION_QUOTES = 75  # API limit is 100 per request

    def __init__(self):
        self.username = os.getenv("TASTY_USER") or os.getenv("TT_USERNAME")
        self.password = os.getenv("TASTY_PASS") or os.getenv("TT_PASSWORD")
        if not self.username or not self.password:
            raise RuntimeError(
                "Set TASTY_USER/TASTY_PASS (or TT_USERNAME/TT_PASSWORD) to use live mode"
            )
        self.password = self.password.strip()
        self.account_id = os.getenv("TASTY_ACCOUNT_ID")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "StratDeck/0.3",
            }
        )
        self._login()
        if not self.account_id:
            self.account_id = self._fetch_default_account()
        self._metrics_cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------- public interface -------------------------

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        sym = symbol.upper()
        instrument = "Index" if sym in self.INDEX_SYMBOLS else "Equity"
        data = self._get_json(f"/market-data/{instrument}/{sym}")
        last = data.get("last") or data.get("mark") or data.get("mid") or data.get("close") or 0.0
        return {"symbol": sym, "last": float(last)}

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> Dict[str, Any]:
        chain = self._get_json(f"/option-chains/{symbol}/nested")
        items = chain.get("data", {}).get("items", [])
        if not items:
            return {"symbol": symbol, "expiry": expiry, "puts": []}
        expirations = items[0].get("expirations", [])
        if not expirations:
            return {"symbol": symbol, "expiry": expiry, "puts": []}
        target = self._select_expiration(expirations, expiry)
        strikes = target.get("strikes", [])
        subset = self._limit_strikes(symbol, strikes)
        quotes = self._fetch_option_quotes([s["put"].strip() for s in subset if s.get("put")])
        puts: List[Dict[str, Any]] = []
        for strike in subset:
            occ = strike.get("put", "").strip()
            quote = quotes.get(occ)
            if not quote:
                continue
            bid = float(quote.get("bid") or 0.0)
            ask = float(quote.get("ask") or 0.0)
            mid = quote.get("mid")
            if mid is None:
                mid = (bid + ask) / 2 if bid and ask else bid or ask or 0.0
            delta = quote.get("delta") or quote.get("theoretical-delta") or 0.0
            puts.append(
                {
                    "symbol": occ,
                    "strike": float(strike.get("strike-price", 0.0) or 0.0),
                    "delta": abs(float(delta)),
                    "bid": bid,
                    "ask": ask,
                    "mid": float(mid),
                    "streamer": strike.get("put-streamer-symbol"),
                }
            )
        return {
            "symbol": symbol,
            "expiry": target.get("expiration-date"),
            "puts": puts,
        }

    def get_account_summary(self) -> Dict[str, Any]:
        if not self.account_id:
            return {}
        data = self._get_json(f"/accounts/{self.account_id}/balances")
        return data.get("data", {})

    def get_positions(self) -> List[Dict[str, Any]]:
        if not self.account_id:
            return []
        data = self._get_json(f"/accounts/{self.account_id}/positions")
        items = data.get("data", {}).get("items", [])
        positions: List[Dict[str, Any]] = []
        for item in items:
            positions.append(
                {
                    "symbol": item.get("symbol"),
                    "instrument_type": item.get("instrument-type"),
                    "qty": float(item.get("quantity", 0) or 0.0),
                    "mark": float(item.get("mark", 0) or 0.0),
                }
            )
        return positions

    def get_ivr(self, symbol: str) -> Optional[float]:
        sym = symbol.upper()
        cached = self._metrics_cache.get(sym)
        if cached and time.time() - cached.get("ts", 0) < 300:
            return cached.get("ivr")
        try:
            data = self._get_json("/market-metrics/IVR", params={"symbol": sym})
        except Exception:
            return None
        rank = data.get("data", {}).get("implied-volatility-index-rank")
        if rank is None:
            return None
        ivr = float(rank)
        self._metrics_cache[sym] = {"ivr": ivr, "ts": time.time()}
        return ivr

    def preview_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._translate_order(order)
        resp = self._request("POST", f"/accounts/{self.account_id}/orders/dry-run", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"Preview failed {resp.status_code}: {resp.text}")
        data = resp.json().get("data") if resp.text else {}
        return data or {"status": "ok"}

    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._translate_order(order)
        resp = self._request("POST", f"/accounts/{self.account_id}/orders", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"Order failed {resp.status_code}: {resp.text}")
        return resp.json().get("data") if resp.text else {"status": "submitted"}

    # ----------------------------- helpers -----------------------------

    def _login(self) -> None:
        resp = self.session.post(
            f"{self.API_BASE}/sessions",
            json={"login": self.username, "password": self.password},
            timeout=30,
        )
        if resp.status_code != 201:
            raise RuntimeError(f"Tastytrade login failed: {resp.text}")
        token = resp.json()["data"]["session-token"]
        self.session.headers["Authorization"] = token
        self._session_token = token
        self._session_created = time.time()

    def _fetch_default_account(self) -> str:
        data = self._get_json("/customers/me/accounts")
        items = data.get("data", {}).get("items", [])
        for item in items:
            account = item.get("account") or {}
            if item.get("authority-level") == "owner" and account.get("account-number"):
                return account["account-number"]
        raise RuntimeError("No tastytrade account found; set TASTY_ACCOUNT_ID")

    def _request(self, method: str, path: str, *, params=None, json=None) -> requests.Response:
        url = f"{self.API_BASE}{path}"
        resp = self.session.request(
            method,
            url,
            params=params,
            json=json,
            timeout=30,
        )
        if resp.status_code == 401:
            self._login()
            resp = self.session.request(
                method,
                url,
                params=params,
                json=json,
                timeout=30,
            )
        return resp

    def _get_json(self, path: str, *, params=None) -> Dict[str, Any]:
        resp = self._request("GET", path, params=params)
        if resp.status_code >= 400:
            raise RuntimeError(f"Tastytrade error {resp.status_code}: {resp.text}")
        if not resp.text:
            return {}
        return resp.json()

    def _select_expiration(
        self, expirations: List[Dict[str, Any]], expiry: Optional[str]
    ) -> Dict[str, Any]:
        if not expiry:
            return expirations[0]
        target = expirations[0]
        best_diff = float("inf")
        desired_dte = self._dte_from_string(expiry)
        for exp in expirations:
            if exp.get("expiration-date") == expiry:
                return exp
            diff = abs(exp.get("days-to-expiration", 0) - desired_dte)
            if diff < best_diff:
                best_diff = diff
                target = exp
        return target

    @staticmethod
    def _dte_from_string(expiry: str) -> int:
        try:
            exp = datetime.strptime(expiry, "%Y-%m-%d").date()
            return max((exp - datetime.utcnow().date()).days, 0)
        except Exception:
            return 0

    def _limit_strikes(self, symbol: str, strikes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not strikes:
            return []
        try:
            price = self.get_quote(symbol).get("last", 0.0)
        except Exception:
            price = 0.0
        sorted_strikes = sorted(
            strikes,
            key=lambda s: abs(float(s.get("strike-price", 0.0) or 0.0) - price),
        )
        return sorted_strikes[: self.MAX_OPTION_QUOTES]

    def _fetch_option_quotes(self, occ_symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        quotes: Dict[str, Dict[str, Any]] = {}
        chunk: List[str] = []
        for sym in occ_symbols:
            sym = sym.strip()
            if not sym:
                continue
            chunk.append(sym)
            if len(chunk) == self.MAX_OPTION_QUOTES:
                quotes.update(self._fetch_quote_chunk(chunk))
                chunk = []
        if chunk:
            quotes.update(self._fetch_quote_chunk(chunk))
        return quotes

    def _fetch_quote_chunk(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        params = [("equity-option", sym) for sym in symbols]
        params.append(("with-greeks", "true"))
        data = self._get_json("/market-data/by-type", params=params)
        items = data.get("data", {}).get("items", [])
        return {item.get("symbol"): item for item in items}

    def _translate_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        legs = []
        total = 0.0
        for leg in order.get("legs", []):
            if leg.get("kind") != "option":
                continue
            occ = self._make_occ_symbol(order.get("symbol", ""), leg.get("expiry", ""), leg.get("type", ""), float(leg.get("strike", 0)))
            side = leg.get("side", "buy").lower()
            action = "Sell to Open" if side == "sell" else "Buy to Open"
            total += (1 if action.startswith("Sell") else -1) * float(order.get("price") or 0.0)
            legs.append({
                "instrument-type": "Equity Option",
                "symbol": occ,
                "quantity": abs(int(leg.get("qty", 1))),
                "action": action,
            })
        payload = {
            "source": "STRATDECK",
            "order-type": "Limit",
            "time-in-force": "Day",
            "price": f"{float(order.get('price') or 0.0):.2f}",
            "price-effect": "Credit" if total >= 0 else "Debit",
            "legs": legs,
        }
        return payload

    def _make_occ_symbol(self, symbol: str, expiry: str, opt_type: str, strike: float) -> str:
        sym = symbol.upper().ljust(6)[:6]
        try:
            exp = datetime.strptime(expiry, "%Y-%m-%d").strftime("%y%m%d")
        except Exception:
            exp = "000000"
        strike_int = int(round(float(strike) * 1000))
        strike_str = f"{strike_int:08d}"
        opt = opt_type.upper()[0]
        return f"{sym}{exp}{opt}{strike_str}"
