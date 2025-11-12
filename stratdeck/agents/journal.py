# stratdeck/agents/journal.py
import csv, json, os, time
from typing import Dict, Optional

JOURNAL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "journal.csv")

def _ensure_headers():
    if not os.path.exists(JOURNAL_PATH):
        os.makedirs(os.path.dirname(JOURNAL_PATH), exist_ok=True)
        with open(JOURNAL_PATH, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts","event","position_id","symbol","text","metrics"])

class JournalAgent:
    def write(self, event: str, position_id: Optional[int], spread_plan: Dict, extra_text: str, metrics: Dict):
        _ensure_headers()
        with open(JOURNAL_PATH, "a", newline="") as f:
            csv.writer(f).writerow([
                int(time.time()),
                event,
                position_id or "",
                spread_plan.get("symbol",""),
                extra_text,
                json.dumps(metrics or {}, separators=(",", ":"))
            ])
        return True

    def log_close(self, position_id: Optional[int], symbol: str, pnl: float, note: str = "", metrics: Optional[Dict] = None):
        payload = metrics.copy() if metrics else {}
        payload.setdefault("pnl", float(pnl))
        self.write("CLOSE", position_id, {"symbol": symbol}, note or "CLOSE", payload)

    def log_open(self, position_id: Optional[int], spread_plan: Dict, qty: int, preview: Dict):
        metrics = {
            "qty": qty,
            "credit": spread_plan.get("credit"),
            "preview": preview,
        }
        self.write("OPEN", position_id, spread_plan, "OPEN", metrics)

    def daily_report(self):
        # lightweight stub; can expand into P&L summaries
        if not os.path.exists(JOURNAL_PATH):
            return "No journal entries yet."
        return "Journal OK. Entries appended."
