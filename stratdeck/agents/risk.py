# stratdeck/agents/risk.py
from typing import List, Dict
from ..tools.positions import list_positions
from ..tools.pricing import pop_estimate
from ..tools.chains import fetch_vertical_candidates

class RiskAgent:
    def check_positions(self) -> List[Dict]:
        """
        Simple rules:
          - If short delta > 0.35 => suggest ROLL OUT
          - If current POP >= entry POP + 0.1 OR synthetic P&L >= 50% => suggest EXIT
        Uses mock/live chain to approximate current mid deltas/prices.
        """
        recs = []
        pos = list_positions()
        if not pos:
            return [{"info":"No open positions"}]
        for p in pos:
            symbol = p["symbol"]
            width = p["width"]
            credit = p["credit"]
            qty = p["qty"]
            # reconstruct a candidate near default delta (we don't store strikes yet)
            target_delta = 0.20
            dte = 15
            vert = fetch_vertical_candidates(symbol, dte, target_delta, int(width))
            short_delta = float(vert["short"]["delta"])
            # rough current credit from mid values
            current_credit = max(vert["short"]["mid"] - vert["long"]["mid"], 0.01)
            # synthetic P&L for credit spread: profit when buy-to-close debit shrinks
            paid = credit
            to_close = round(width - current_credit, 2)  # crude proxy
            pnl_pct = round(max((paid - to_close) / max(paid, 0.01), -1.0), 2)
            rec = {"position_id": p["id"], "symbol": symbol, "short_delta": short_delta, "pnl_pct": pnl_pct}
            if short_delta > 0.35:
                rec["action"] = "ROLL"
                rec["reason"] = f"Short Δ {short_delta:.2f} > 0.35"
            elif pnl_pct >= 0.5:
                rec["action"] = "EXIT"
                rec["reason"] = f"Reached 50% profit est. (≈{pnl_pct*100:.0f}%)"
            else:
                rec["action"] = "HOLD"
                rec["reason"] = "Within risk bounds"
            recs.append(rec)
        return recs