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
        payload = self._get_json(f"/market-data/{instrument}/{sym}")
        data = payload.get("data") or payload or {}

        def _f(val: Any) -> Optional[float]:
            try:
                return float(val)
            except Exception:
                return None

        bid = _f(data.get("bid") or data.get("best-bid") or data.get("bid-price"))
        ask = _f(data.get("ask") or data.get("best-ask") or data.get("ask-price"))
        last = _f(data.get("last") or data.get("last-price") or data.get("close"))
        mark = _f(data.get("mark") or data.get("mark-price"))
        mid = self._mid(bid, ask, mark, last)
        return {
            "symbol": sym,
            "bid": bid,
            "ask": ask,
            "last": last if last is not None else (mid if mid is not None else 0.0),
            "mark": mid,
            "mid": mid,
        }

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> Dict[str, Any]:
        chain = self._get_json(f"/option-chains/{symbol}/nested")
        items = chain.get("data", {}).get("items", [])
        if not items:
            return {"symbol": symbol, "expiry": expiry, "puts": [], "calls": []}
        expirations = items[0].get("expirations", [])
        if not expirations:
            return {"symbol": symbol, "expiry": expiry, "puts": [], "calls": []}
        target = self._select_expiration(expirations, expiry)
        strikes = self._limit_strikes(symbol, target.get("strikes", []))
        if not strikes:
            return {"symbol": symbol, "expiry": target.get("expiration-date"), "puts": [], "calls": []}

        occs = []
        for strike in strikes:
            for key in ("put", "call"):
                occ = strike.get(key)
                if occ:
                    occs.append(occ.strip())
        occs = [o for o in occs if o][: self.MAX_OPTION_QUOTES]

        quotes = self._fetch_option_quotes(occs)
        puts: List[Dict[str, Any]] = []
        calls: List[Dict[str, Any]] = []

        for strike in strikes:
            for opt_type in ("put", "call"):
                occ_symbol = (strike.get(opt_type) or "").strip()
                if not occ_symbol:
                    continue
                parsed = self._option_row(strike, opt_type, occ_symbol, quotes)
                if not parsed:
                    continue
                if opt_type == "put":
                    puts.append(parsed)
                else:
                    calls.append(parsed)

        return {
            "symbol": symbol,
            "expiry": target.get("expiration-date"),
            "puts": puts,
            "calls": calls,
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
        if not symbols:
            return {}
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
            occ = self._make_occ_symbol(
                order.get("symbol", ""),
                leg.get("expiry", ""),
                leg.get("type", ""),
                float(leg.get("strike", 0)),
            )
            side = leg.get("side", "buy").lower()
            action = "Sell to Open" if side == "sell" else "Buy to Open"
            total += (1 if action.startswith("Sell") else -1) * float(order.get("price") or 0.0)
            legs.append(
                {
                    "instrument-type": "Equity Option",
                    "symbol": occ,
                    "quantity": abs(int(leg.get("qty", 1))),
                    "action": action,
                }
            )
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

    # ----------------------------- parsing helpers -------------------------

    @staticmethod
    def _mid(bid: Optional[float], ask: Optional[float], mark: Optional[float], fallback: Optional[float] = None) -> Optional[float]:
        vals = [v for v in (mark, fallback) if v is not None]
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            vals.insert(0, (bid + ask) / 2.0)
        return vals[0] if vals else None

    @staticmethod
    def _safe_float(val: Any) -> Optional[float]:
        try:
            return float(val)
        except Exception:
            return None

    def _option_row(
        self,
        strike_row: Dict[str, Any],
        opt_type: str,
        occ_symbol: str,
        quotes: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        quote = quotes.get(occ_symbol)
        if not quote:
            return None

        bid = self._safe_float(quote.get("bid"))
        ask = self._safe_float(quote.get("ask"))
        mark = self._safe_float(quote.get("mark") or quote.get("mid"))
        last = self._safe_float(quote.get("last"))
        mid = self._mid(bid, ask, mark, last) or 0.0
        greeks = self._extract_greeks(quote)
        delta = greeks.get("delta") or self._safe_float(quote.get("delta")) or self._safe_float(
            quote.get("theoretical-delta")
        )
        strike_price = self._safe_float(strike_row.get("strike-price")) or 0.0

        row = {
            "symbol": occ_symbol,
            "type": opt_type,
            "strike": strike_price,
            "bid": bid if bid is not None else 0.0,
            "ask": ask if ask is not None else 0.0,
            "last": last if last is not None else mid,
            "mid": float(mid),
            "delta": abs(float(delta)) if delta is not None else 0.0,
            "streamer": strike_row.get(f"{opt_type}-streamer-symbol"),
        }

        if greeks:
            row["greeks"] = greeks
            for k, v in greeks.items():
                row[k] = v

        return row

    def _extract_greeks(self, quote: Dict[str, Any]) -> Dict[str, float]:
        greeks: Dict[str, float] = {}
        quote_greeks = quote.get("greeks") or {}
        for key in ("delta", "theta", "gamma", "vega"):
            raw = (
                quote.get(key)
                or quote_greeks.get(key)
                or quote.get(f"theoretical-{key}")
                or quote_greeks.get(f"theoretical-{key}")
            )
            try:
                if raw is not None:
                    greeks[key] = float(raw)
            except Exception:
                continue
        return greeks
