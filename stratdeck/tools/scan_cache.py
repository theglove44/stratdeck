from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List


@dataclass
class ScanCache:
    rows: List[Dict[str, Any]] = field(default_factory=list)
    ideas: List[Any] = field(default_factory=list)


_scan_cache = ScanCache()


def store_scan_rows(rows: Iterable[Dict[str, Any]]) -> None:
    """
    Store the latest raw scan rows so other commands can reference them.
    """
    global _scan_cache
    _scan_cache.rows = list(rows)
    _scan_cache.ideas = []


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
