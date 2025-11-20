# stratdeck/tools/positions.py
import csv, json, os, time
from datetime import datetime, timezone
from typing import Any, List, Dict, Optional

POS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "positions.csv")
DEFAULT_FIELDNAMES = [
    "id",
    "ts",
    "symbol",
    "underlying",
    "strategy",
    "direction",
    "expiry",
    "dte",
    "width",
    "credit",
    "entry_mid_price",
    "qty",
    "status",
    "exit_credit",
    "pnl",
    "closed_ts",
    "provenance",
    "notes",
    "account_id",
]
LEGACY_FIELDNAMES = ["id","ts","symbol","strategy","expiry","width","credit","qty","status","exit_credit","pnl","closed_ts"]


def _calc_dte(expiry: Optional[str]) -> Optional[int]:
    if not expiry:
        return None
    try:
        d = datetime.fromisoformat(str(expiry)).date()
    except Exception:
        return None
    today = datetime.now(timezone.utc).date()
    return max((d - today).days, 0)

def _resolve_fieldnames(existing: Optional[List[str]] = None) -> List[str]:
    fields = list(existing or [])
    if not fields:
        fields = list(DEFAULT_FIELDNAMES)
    for fname in DEFAULT_FIELDNAMES:
        if fname not in fields:
            fields.append(fname)
    return fields


def _write_rows(rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    os.makedirs(os.path.dirname(POS_PATH), exist_ok=True)
    with open(POS_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({fn: row.get(fn, "") for fn in fieldnames})


def _ensure_headers() -> List[str]:
    if not os.path.exists(POS_PATH):
        _write_rows([], DEFAULT_FIELDNAMES)
        return DEFAULT_FIELDNAMES

    with open(POS_PATH, "r", newline="") as f:
        reader = csv.DictReader(f)
        existing_fields = reader.fieldnames or LEGACY_FIELDNAMES
        merged = _resolve_fieldnames(existing_fields)
        missing = [f for f in merged if f not in (reader.fieldnames or [])]
        if missing:
            rows = list(reader)
            _write_rows(rows, merged)
        return merged


def _read_all() -> List[Dict]:
    fieldnames = _ensure_headers()
    rows: List[Dict] = []
    with open(POS_PATH, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            for key in fieldnames:
                row.setdefault(key, "")
            rows.append(row)
    return rows


def _write_all(rows: List[Dict]) -> None:
    fieldnames = _resolve_fieldnames(rows[0].keys() if rows else DEFAULT_FIELDNAMES)
    _write_rows(rows, fieldnames)


def add_position(
    spread_plan: Dict,
    qty: int,
    *,
    entry_mid_price: Optional[float] = None,
    account_id: Optional[str] = None,
) -> Dict:
    fieldnames = _ensure_headers()
    pid = int(time.time() * 1000)
    ts = int(time.time())
    symbol = spread_plan.get("symbol") or spread_plan.get("underlying")
    underlying = spread_plan.get("underlying") or symbol
    strategy = spread_plan.get("strategy", "")
    direction = spread_plan.get("direction", "")
    expiry = spread_plan.get("expiry", "")
    width = spread_plan.get("width", "")
    credit = spread_plan.get("credit", 0.0)
    dte = spread_plan.get("dte")
    if dte is None:
        dte = _calc_dte(expiry)
    notes_val = spread_plan.get("notes", "")
    if isinstance(notes_val, list):
        notes_val = "; ".join([str(n) for n in notes_val])
    provenance = spread_plan.get("provenance")
    if isinstance(provenance, dict):
        try:
            provenance = json.dumps(provenance, sort_keys=True)
        except Exception:
            provenance = str(provenance)

    entry_mid = credit if entry_mid_price is None else entry_mid_price

    row = {
        "id": pid,
        "ts": ts,
        "symbol": symbol,
        "underlying": underlying,
        "strategy": strategy,
        "direction": direction,
        "expiry": expiry,
        "dte": dte if dte is not None else "",
        "width": width,
        "credit": credit,
        "entry_mid_price": entry_mid,
        "qty": qty,
        "status": "OPEN",
        "exit_credit": "",
        "pnl": "",
        "closed_ts": "",
        "provenance": provenance or "",
        "notes": notes_val or "",
        "account_id": account_id or "",
    }

    all_rows = _read_all()
    all_rows.append(row)
    _write_all(all_rows)
    return {"id": pid}

def list_positions() -> List[Dict]:
    if not os.path.exists(POS_PATH):
        return []
    out = []
    with open(POS_PATH, "r", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                row["id"] = int(row["id"])
            except Exception:
                continue
            try:
                row["width"] = float(row["width"]) if row.get("width") else None
            except Exception:
                row["width"] = None
            try:
                row["credit"] = float(row["credit"]) if row.get("credit") else 0.0
            except Exception:
                row["credit"] = 0.0
            try:
                row["entry_mid_price"] = (
                    float(row["entry_mid_price"]) if row.get("entry_mid_price") else row.get("credit")
                )
            except Exception:
                row["entry_mid_price"] = row.get("credit")
            try:
                row["qty"] = int(row["qty"])
            except Exception:
                row["qty"] = 0
            row["exit_credit"] = float(row["exit_credit"]) if row.get("exit_credit") else None
            row["pnl"] = float(row["pnl"]) if row.get("pnl") else None
            row["closed_ts"] = int(row["closed_ts"]) if row.get("closed_ts") else None
            row["dte"] = int(row["dte"]) if row.get("dte") not in ("", None) else None
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
