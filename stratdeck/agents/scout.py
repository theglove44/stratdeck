from typing import List, Dict, Optional, Any
import math
from datetime import datetime

from ..core.config import cfg
from ..core.scoring import score_candidate
from ..data.factory import get_provider
from ..tools.account import is_live_mode
from ..tools.chains import fetch_vertical_candidates
from ..tools.vol import load_snapshot

class ScoutAgent:
    """
    MVP ScoutAgent that doesn't need external APIs yet.
    It:
      - reads config + IVR snapshot
      - creates one vertical per symbol at target delta and configured width
      - estimates credit and POP heuristically
      - tags liquidity based on simple proxy (index ETFs good, else neutral)
    Replace the estimator methods with real chains/pricing when ready.
    """

    def __init__(self):
        self.C = cfg()
        self.IVR = load_snapshot()
        self.live_mode = is_live_mode()
        self.provider = get_provider() if self.live_mode else None

    def run(self) -> List[Dict]:
        watchlist = self.C.get("watchlist", [])
        results = []
        for sym in watchlist:
            cand = self._build_candidate(sym)
            if not cand:
                continue
            cand["score"] = score_candidate(cand)
            results.append(cand)

        # rank high to low
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    # --------- internals ---------

    def _width_for(self, symbol: str) -> int:
        w = self.C.get("width_rules", {})
        return int(w.get(symbol, w.get("DEFAULT", 1)))

    def _target_delta(self) -> float:
        return float(self.C["entries"].get("delta_short", 0.20))

    def _min_credit_ratio(self) -> float:
        return float(self.C["entries"].get("min_credit_ratio", 0.01))

    def _pop_floor(self) -> float:
        from ..core.config import scoring_conf
        return float(scoring_conf()["thresholds"].get("min_pop", 0.55))

    def _estimate_credit(self, width: float, ivr: float) -> float:
        """
        Heuristic: higher IVR => richer credit.
        Roughly 20%..40% of width scaled by IVR.
        """
        base = 0.2 + 0.2 * max(min(ivr, 1.0), 0.0)  # 0.2..0.4
        credit = round(width * base, 2)
        return max(0.01, credit)

    def _estimate_pop(self, target_delta: float, ivr: float) -> float:
        """
        Heuristic POP ~ 1 - target_delta adjusted slightly by IVR skew.
        """
        base = 1.0 - target_delta
        adj = (0.5 - ivr) * 0.05  # tiny nudge
        pop = max(0.50, min(0.95, base + adj))
        return round(pop, 2)

    def _liquidity_tag(self, symbol: str) -> str:
        liquid_syms = {"SPX", "XSP", "QQQ", "IWM", "SPY"}
        return "GOOD" if symbol in liquid_syms else "OK"

    def _build_candidate(self, symbol: str) -> Dict:
        if self.provider:
            live = self._build_live_candidate(symbol)
            if live:
                return live
        return self._build_mock_candidate(symbol)

    def _build_mock_candidate(self, symbol: str) -> Dict:
        width = self._width_for(symbol)
        ivr = float(self.IVR.get(symbol, 0.25))
        dte = int(self.C["entries"].get("default_dte", 30))
        target_delta = self._target_delta()
        credit = self._estimate_credit(width, ivr)
        pop = self._estimate_pop(target_delta, ivr)
        liq = self._liquidity_tag(symbol)
        credit_ratio = credit / max(width, 0.01)
        if credit_ratio < self._min_credit_ratio() or pop < self._pop_floor():
            return {}
        return {
            "symbol": symbol,
            "strategy": "PUT_CREDIT",
            "dte": dte,
            "width": width,
            "credit": credit,
            "pop": pop,
            "liquidity": liq,
            "ivr": round(ivr, 2),
            "rationale": f"IVR {ivr:.2f}, est credit {credit:.2f} on {width}-wide, POP {pop:.2f} near Δ={target_delta:.2f}",
        }

    def _build_live_candidate(self, symbol: str) -> Dict:
        try:
            dte = int(self.C["entries"].get("default_dte", 30))
            target_delta = self._target_delta()
            width_hint = self._width_for(symbol)
            vertical = fetch_vertical_candidates(symbol, dte, target_delta, width_hint)
        except Exception:
            return {}
        short = vertical.get("short") or {}
        long = vertical.get("long") or {}
        if not short or not long:
            return {}
        short_strike = float(short.get("strike", 0.0) or 0.0)
        long_strike = float(long.get("strike", 0.0) or 0.0)
        actual_width = abs(short_strike - long_strike) or width_hint or 1
        short_mid = self._mid_price(short)
        long_mid = self._mid_price(long)
        credit = round(max(short_mid - long_mid, 0.01), 2)
        delta = abs(float(short.get("delta") or target_delta))
        pop = round(max(0.50, min(0.95, 1.0 - delta)), 2)
        liq = self._liquidity_tag(symbol)
        ivr = self._live_ivr(symbol)
        if ivr is None:
            ivr = float(self.IVR.get(symbol, 0.25))
        credit_ratio = credit / max(actual_width, 0.01)
        if credit_ratio < self._min_credit_ratio() or pop < self._pop_floor():
            return {}
        expiry = vertical.get("expiry")
        dte_actual = self._dte_from_expiry(expiry) if expiry else int(self.C["entries"].get("default_dte", 30))
        rationale = (f"short {short_strike:.2f}Δ{delta:.2f} long {long_strike:.2f} "
                     f"credit {credit:.2f} on {actual_width:.2f}-wide")
        return {
            "symbol": symbol,
            "strategy": "PUT_CREDIT",
            "dte": dte_actual,
            "width": round(actual_width, 2),
            "credit": credit,
            "pop": pop,
            "liquidity": liq,
            "ivr": round(float(ivr), 3),
            "short_strike": short_strike,
            "long_strike": long_strike,
            "short_delta": delta,
            "expiry": expiry,
            "rationale": rationale,
        }

    def _mid_price(self, leg: Dict[str, Any]) -> float:
        bid = leg.get("bid")
        ask = leg.get("ask")
        mid = leg.get("mid")
        try:
            bid = float(bid) if bid is not None else None
        except Exception:
            bid = None
        try:
            ask = float(ask) if ask is not None else None
        except Exception:
            ask = None
        if mid is not None:
            try:
                return float(mid)
            except Exception:
                pass
        if bid and ask:
            return round((bid + ask) / 2, 4)
        return bid or ask or 0.0

    def _live_ivr(self, symbol: str) -> Optional[float]:
        if not self.provider:
            return None
        try:
            return self.provider.get_ivr(symbol)
        except Exception:
            return None

    def _dte_from_expiry(self, expiry: str) -> int:
        try:
            exp = datetime.strptime(expiry, "%Y-%m-%d").date()
            today = datetime.utcnow().date()
            return max((exp - today).days, 0)
        except Exception:
            return int(self.C["entries"].get("default_dte", 30))
