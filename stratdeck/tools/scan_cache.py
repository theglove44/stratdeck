from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional


@dataclass
class ScanCache:
    """
    In-memory cache of the last scan results and the trade ideas derived from them.

    - rows: raw scan rows (dicts) used as input to TradePlanner.
    - ideas: TradeIdea objects (or dicts) emitted by TradePlanner.
    """
    rows: List[Dict[str, Any]] = field(default_factory=list)
    ideas: List[Any] = field(default_factory=list)


_scan_cache = ScanCache()


def store_scan_rows(rows: Iterable[Mapping[str, Any]]) -> None:
    """
    Save the most recent scan rows. The caller typically passes in a list of dicts
    built by the scan/TA pipeline.
    """
    global _scan_cache
    # Normalise to plain dicts so later code can safely mutate copies.
    _scan_cache.rows = [dict(r) for r in rows]


def store_trade_ideas(ideas: Iterable[Any]) -> None:
    """
    Save the most recent TradeIdea list for follow-up commands.
    """
    global _scan_cache
    _scan_cache.ideas = list(ideas)


def load_last_scan() -> ScanCache:
    """
    Return the last stored scan payload. The caller can inspect .rows and .ideas.
    """
    return _scan_cache


def attach_ivr_to_scan_rows(
    rows: Iterable[Mapping[str, Any]],
    iv_snapshot: Mapping[str, Mapping[str, Any]],
    symbol_keys: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Given raw scan rows and an IV snapshot (e.g. loaded from iv_snapshot.json),
    return a new list of rows with an 'ivr' field attached where available.

    - rows: iterable of scan row dict-like objects. Must contain at least one of
      the keys given in symbol_keys (defaults to ['data_symbol', 'symbol']).
    - iv_snapshot: mapping like { 'SPX': {'ivr': 0.32, ...}, ... } (ivr 0–1)
    - symbol_keys: priority list of keys to use to look up the symbol in each row.

    This is the right place for the IVR wiring (step 2.2): call this once in the
    CLI before passing scan_rows into TradePlanner.
    """
    if symbol_keys is None:
        symbol_keys = ["symbol", "data_symbol"]

    result: List[Dict[str, Any]] = []

    for row in rows:
        base = dict(row)

        symbol: Optional[str] = None
        for key in symbol_keys:
            val = base.get(key)
            if isinstance(val, str) and val:
                symbol = val
                break

        if symbol is not None:
            vol_info = iv_snapshot.get(symbol, {})
            ivr = vol_info.get("ivr")
            if ivr is not None:
                base["ivr"] = ivr

        result.append(base)

    return result
