# stratdeck/tools/positions.py
import csv, os, time
from typing import List, Dict, Optional

POS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "positions.csv")

def _ensure_headers():
    if not os.path.exists(POS_PATH):
        os.makedirs(os.path.dirname(POS_PATH), exist_ok=True)
        with open(POS_PATH, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id","ts","symbol","strategy","expiry","width","credit","qty","status","exit_credit","pnl","closed_ts"])

def _read_all() -> List[Dict]:
    _ensure_headers()
    rows: List[Dict] = []
    with open(POS_PATH, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows

def _write_all(rows: List[Dict]) -> None:
    _ensure_headers()
    with open(POS_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id","ts","symbol","strategy","expiry","width","credit","qty","status","exit_credit","pnl","closed_ts"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

def add_position(spread_plan: Dict, qty: int) -> Dict:
    _ensure_headers()
    pid = int(time.time()*1000)
    row = [
        pid,
        int(time.time()),
        spread_plan["symbol"],
        spread_plan["strategy"],
        spread_plan.get("expiry",""),
        spread_plan["width"],
        spread_plan["credit"],
        qty,
        "OPEN",
        "",
        "",
        ""
    ]
    with open(POS_PATH, "a", newline="") as f:
        csv.writer(f).writerow(row)
    return {"id": pid}

def list_positions() -> List[Dict]:
    if not os.path.exists(POS_PATH):
        return []
    out = []
    with open(POS_PATH, "r") as f:
        r = csv.DictReader(f)
        for row in r:
            row["id"] = int(row["id"])
            row["width"] = float(row["width"])
            row["credit"] = float(row["credit"])
            row["qty"] = int(row["qty"])
            row["exit_credit"] = float(row["exit_credit"]) if row.get("exit_credit") else None
            row["pnl"] = float(row["pnl"]) if row.get("pnl") else None
            row["closed_ts"] = int(row["closed_ts"]) if row.get("closed_ts") else None
            out.append(row)
    return out

def close_position(position_id: int, exit_credit: float) -> Dict:
    rows = _read_all()
    updated = False
    result: Optional[Dict] = None
    for row in rows:
        try:
            rid = int(row["id"])
        except (ValueError, TypeError):
            continue
        if rid != position_id:
            continue
        if row.get("status") == "CLOSED":
            raise ValueError(f"Position {position_id} already closed")
        entry_credit = float(row.get("credit", 0.0) or 0.0)
        qty = int(row.get("qty", 1) or 1)
        pnl = (entry_credit - float(exit_credit)) * qty * 100.0
        row["status"] = "CLOSED"
        row["exit_credit"] = f"{float(exit_credit):.2f}"
        row["pnl"] = f"{pnl:.2f}"
        row["closed_ts"] = str(int(time.time()))
        updated = True
        result = {
            "id": position_id,
            "symbol": row.get("symbol"),
            "entry_credit": entry_credit,
            "exit_credit": float(exit_credit),
            "qty": qty,
            "pnl": pnl,
        }
        break
    if not updated:
        raise ValueError(f"Position {position_id} not found")
    _write_all(rows)
    return result
