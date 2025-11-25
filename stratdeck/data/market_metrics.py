from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Mapping, Sequence

import requests

from .tasty_provider import API_BASE, make_tasty_session_from_env

logger = logging.getLogger(__name__)


def _extract_ivr_from_item(item: Mapping[str, Any]) -> Optional[float]:
    """
    Extract a 0–1 IV Rank from a Tasty market-metrics item.

    We prefer explicit keys, but will fall back to *any* key that looks like
    an implied-volatility-index-rank value (excluding 'source' fields).
    """
    if not isinstance(item, Mapping):
        return None

    # 1) Preferred keys (what the tests expect)
    preferred_keys = [
        "tw-implied-volatility-index-rank",   # tasty-specific
        "implied-volatility-index-rank",      # generic
        "tos-implied-volatility-index-rank",  # thinkorswim
    ]

    raw = None
    for key in preferred_keys:
        v = item.get(key)
        if v is not None:
            raw = v
            break

    # 2) Fallback: scan for any rank-like key if none of the preferred ones hit.
    if raw is None:
        for key, v in item.items():
            # we don't care about 'source' keys, only numeric ranks
            if "implied-volatility-index-rank" in key and "source" not in key:
                raw = v
                break

    if raw is None:
        return None

    # 3) Normalise value: handle strings, '%', etc.
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.endswith("%"):
            raw = raw[:-1].strip()

    try:
        ivr = float(raw)
    except (TypeError, ValueError):
        return None

    # 4) Scale to 0–1 range:
    # - If it's already in 0–1-ish, accept.
    # - If it's 0–100-ish, divide by 100.
    if 0.0 <= ivr <= 1.5:
        pass
    elif 1.5 < ivr <= 150.0:
        ivr = ivr / 100.0
    else:
        return None

    # Final sanity clamp
    if ivr < 0.0 or ivr > 1.0:
        return None

    return ivr


def _items_from_response(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalise market-metrics payloads to a list of item dicts."""
    if not isinstance(payload, dict):
        return []

    data = payload.get("data") or payload
    items = data.get("items")
    if not isinstance(items, list):
        return []

    return [i for i in items if isinstance(i, dict)]


DEFAULT_CHUNK_SIZE = 50
MAX_RETRIES_PER_CHUNK = 2  # 1 initial + 1 retry


def fetch_iv_rank_for_symbols(
    symbols: Sequence[str],
    *,
    session: Optional[requests.Session] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Dict[str, float]:
    if not symbols:
        return {}

    syms = sorted({(s or "").upper() for s in symbols})

    if session is None:
        session = make_tasty_session_from_env()

    out: Dict[str, float] = {}

    for i in range(0, len(syms), chunk_size):
        chunk = syms[i : i + chunk_size]
        if not chunk:
            continue

        params = {"symbols": ",".join(chunk)}

        attempts = 0
        while attempts < MAX_RETRIES_PER_CHUNK:
            attempts += 1
            chunk_hits = 0
            try:
                resp = session.get(
                    f"{API_BASE}/market-metrics",
                    params=params,
                    timeout=30,
                )
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Error fetching market-metrics chunk (attempt %s/%s): %s",
                    attempts,
                    MAX_RETRIES_PER_CHUNK,
                    exc,
                )
                continue

            if resp.status_code >= 400:
                logger.warning(
                    "market-metrics request failed (attempt %s/%s): "
                    "status=%s symbols=%s",
                    attempts,
                    MAX_RETRIES_PER_CHUNK,
                    resp.status_code,
                    ",".join(chunk),
                )
                continue

            try:
                payload = resp.json()
            except ValueError:
                logger.warning(
                    "market-metrics response is not JSON "
                    "(attempt %s/%s) symbols=%s",
                    attempts,
                    MAX_RETRIES_PER_CHUNK,
                    ",".join(chunk),
                )
                continue

            for item in _items_from_response(payload):
                sym = (item.get("symbol") or "").upper()
                if not sym:
                    continue
                ivr = _extract_ivr_from_item(item)
                if ivr is None:
                    continue
                out[sym] = ivr
                chunk_hits += 1

            if chunk_hits == 0:
                logger.warning(
                    "no IVR values in market-metrics for symbols=%s",
                    ",".join(chunk),
                )

            break  # success for this chunk; move to next

        # if all attempts fail, we just move on to next chunk

    return out
