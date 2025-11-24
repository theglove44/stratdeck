"""Helpers for working with Tastytrade watchlists.

This module intentionally keeps the surface area small: fetch a watchlist by
name and return the underlying symbols in a deterministic order.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from .tasty_provider import API_BASE, make_tasty_session_from_env

log = logging.getLogger(__name__)


def _extract_entries(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize various watchlist response shapes into a list of entries."""

    def _from_candidate(candidate: Any) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(candidate, dict):
            return None

        # Direct list fields commonly used by Tasty APIs.
        for key in (
            "items",
            "watchlist-items",
            "watchlist_items",
            "watchlist-entries",   # <– your summary shape
            "watchlist_entries",
        ):
            entries = candidate.get(key)
            if isinstance(entries, list):
                return entries

        # Nested container under watchlist-entries / watchlist_entries
        nested = candidate.get("watchlist-entries") or candidate.get("watchlist_entries")
        if isinstance(nested, dict):
            for key in ("items", "data"):
                nested_items = nested.get(key)
                if isinstance(nested_items, list):
                    return nested_items

        return None

    # Try the payload itself, then any nested "data" object.
    candidates: Iterable[Any]
    if isinstance(payload, dict):
        candidates = (payload, payload.get("data"))
    else:
        candidates = (payload,)

    for c in candidates:
        if c is None:
            continue
        entries = _from_candidate(c)
        if entries is not None:
            return entries

    return []


def _extract_underlying_symbol(entry: Dict[str, Any]) -> Optional[str]:
    if not isinstance(entry, dict):
        return None

    for key in ("underlying-symbol", "underlying_symbol"):
        val = entry.get(key)
        if val:
            return str(val).strip().upper()

    for key in ("root-symbol", "root_symbol"):
        val = entry.get(key)
        if val:
            return str(val).strip().upper()

    symbol = entry.get("symbol") or entry.get("symbol-symbol") or entry.get("symbol_symbol")
    if not symbol:
        return None

    instrument_type = str(
        entry.get("instrument-type")
        or entry.get("instrument_type")
        or entry.get("instrumentType")
        or ""
    ).lower()

    symbol_str = str(symbol).strip()
    if "option" in instrument_type and " " in symbol_str:
        return symbol_str.split()[0].upper()

    return symbol_str.upper()


def _find_watchlist_by_name(payload: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        items = data.get("items") or []
        if isinstance(items, list):
            candidates.extend([c for c in items if isinstance(c, dict)])
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            candidates.extend([c for c in items if isinstance(c, dict)])

    for wl in candidates:
        if wl.get("name") == name or wl.get("watchlist-name") == name:
            return wl
    return None


def get_watchlist_symbols(name: str) -> List[str]:
    """Return the underlying symbols from the given Tasty watchlist name.

    Network calls are intentionally thin wrappers over the existing Tasty
    session helper; tests are expected to monkeypatch the session layer.
    """

    session = make_tasty_session_from_env()

    resp = session.get(f"{API_BASE}/watchlists", timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"Failed to fetch watchlists: {resp.status_code} {resp.text}")

    payload = resp.json() if resp.text else {}
    watchlist = _find_watchlist_by_name(payload, name)
    if watchlist is None:
        raise RuntimeError(f"Watchlist '{name}' not found")

    entries = _extract_entries(watchlist)
    if not entries:
        watchlist_id = watchlist.get("id") or watchlist.get("watchlist-id")
        if watchlist_id:
            try:
                detail = session.get(
                    f"{API_BASE}/watchlists/{watchlist_id}",
                    timeout=30,
                )
                if detail.status_code < 400 and detail.text:
                    entries = _extract_entries(detail.json())
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("Failed to fetch watchlist entries for %s: %r", name, exc)

    symbols = set()
    for entry in entries:
        sym = _extract_underlying_symbol(entry)
        if sym:
            symbols.add(sym)

    return sorted(symbols)
