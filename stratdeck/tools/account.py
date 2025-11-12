from __future__ import annotations

import os
from typing import Dict

from stratdeck.data.factory import get_provider


def is_live_mode() -> bool:
    return os.getenv("STRATDECK_DATA_MODE", "mock").lower() == "live"


def provider_account_summary() -> Dict:
    try:
        summary = get_provider().get_account_summary() or {}
        return summary
    except NotImplementedError:
        return {}
    except Exception as exc:
        print(f"[account] warn: provider account summary failed ({exc})")
        return {}


def provider_positions_state() -> Dict[str, int]:
    try:
        positions = get_provider().get_positions() or []
    except NotImplementedError:
        positions = []
    except Exception as exc:
        print(f"[account] warn: provider positions failed ({exc})")
        positions = []

    state: Dict[str, int] = {}
    for pos in positions:
        symbol = str(pos.get("symbol")) if pos.get("symbol") else None
        if not symbol:
            continue
        qty = abs(int(pos.get("qty", 1))) if isinstance(pos.get("qty"), (int, float)) else 1
        state[symbol] = state.get(symbol, 0) + max(1, qty)
    return state
