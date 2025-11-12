from __future__ import annotations

import ast
import csv
import json
import os
import time
from typing import Dict, List

from stratdeck.agents.journal import JOURNAL_PATH
from stratdeck.tools.positions import list_positions
from stratdeck.tools.account import provider_account_summary, is_live_mode

SECONDS_PER_DAY = 86400


def _parse_metrics(raw: str) -> Dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        try:
            return dict(ast.literal_eval(raw)) if raw.startswith("{") else {}
        except Exception:
            return {}


def load_journal_entries(days: int = 1) -> List[Dict]:
    if not os.path.exists(JOURNAL_PATH):
        return []
    cutoff = time.time() - max(days, 1) * SECONDS_PER_DAY
    out: List[Dict] = []
    with open(JOURNAL_PATH, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = int(row.get("ts", 0))
            except ValueError:
                continue
            if ts < cutoff:
                continue
            entry = {
                "ts": ts,
                "event": row.get("event", ""),
                "position_id": row.get("position_id"),
                "symbol": row.get("symbol", ""),
                "text": row.get("text", ""),
                "metrics": _parse_metrics(row.get("metrics", "")),
            }
            out.append(entry)
    return out


def summarize_daily(days: int = 1) -> Dict:
    entries = load_journal_entries(days)
    opens = [e for e in entries if e["event"] == "OPEN"]
    closes = [e for e in entries if e["event"] == "CLOSE"]
    realized_pnl = sum(float(e["metrics"].get("pnl", 0.0) or 0.0) for e in closes)
    wins = sum(1 for e in closes if float(e["metrics"].get("pnl", 0.0) or 0.0) > 0)
    losses = sum(1 for e in closes if float(e["metrics"].get("pnl", 0.0) or 0.0) < 0)
    win_rate = (wins / max(1, wins + losses)) * 100.0

    positions = list_positions()
    open_positions = [p for p in positions if p.get("status", "OPEN") != "CLOSED"]
    closed_positions = [p for p in positions if p.get("status", "OPEN") == "CLOSED"]

    summary = {
        "opened": len(opens),
        "closed": len(closes),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "realized_pnl": realized_pnl,
        "open_positions": len(open_positions),
        "closed_positions": len(closed_positions),
        "live_account": provider_account_summary() if is_live_mode() else {},
    }
    return summary
