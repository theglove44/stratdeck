from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from stratdeck.data.market_metrics import fetch_iv_rank_for_symbols

# Default path: stratdeck/data/iv_snapshot.json
IV_SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "iv_snapshot.json"


def resolve_live_universe_symbols() -> set[str]:
    """Return the set of symbols for which we want IV Rank."""
    from stratdeck.data.factory import get_live_universe_symbols

    return set(get_live_universe_symbols())


def build_iv_snapshot(path: Path = IV_SNAPSHOT_PATH) -> Dict[str, Dict[str, float]]:
    """Build and write an IV snapshot JSON file with nested shape."""
    symbols = sorted(resolve_live_universe_symbols())
    if not symbols:
        snapshot: Dict[str, Dict[str, float]] = {}
    else:
        ivr_map = fetch_iv_rank_for_symbols(symbols)
        snapshot = {sym: {"ivr": float(ivr)} for sym, ivr in sorted(ivr_map.items())}

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)

    return snapshot
