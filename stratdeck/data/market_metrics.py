from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Mapping, Sequence

import requests

from .tasty_provider import API_BASE, make_tasty_session_from_env

logger = logging.getLogger(__name__)

# IV Rank (IVR) extraction
#
# We have confirmed that the Tasty watchlist “IV Rank” column is backed by the
# `/market-metrics` field `implied-volatility-index-rank` (TOS source).
#
# StratDeck normalises this field to a 0–1 float called `ivr`:
#   - 0   → 0.0
#   - 100 → 1.0
#
# If `implied-volatility-index-rank` is missing, we fall back to other
# *-implied-volatility-index-rank fields when available. Any 0–100 values are
# scaled down to 0–1 and clamped.

CANONICAL_IVR_FIELDS = [
    "implied-volatility-index-rank",  # TOS/generic
    "tw-implied-volatility-index-rank",  # tasty-specific UI column
]

FALLBACK_IVR_FIELDS = [
    "tos-implied-volatility-index-rank",
    # add others only if observed in /market-metrics payloads
]


def _extract_ivr_from_item(item: Mapping[str, Any]) -> Optional[float]:
    """
    Extract IV Rank as a 0–1 float from a /market-metrics item.

    Canonical source is `implied-volatility-index-rank`, which backs the
    Tasty UI “IV Rank” column (TOS source). We normalise to 0–1 and clamp.
    """
    raw = None

    # Prefer canonical field(s)
    for key in CANONICAL_IVR_FIELDS:
        val = item.get(key)
        if val is not None:
            raw = val
            break

    # Fallbacks if canonical is missing
    if raw is None:
        for key in FALLBACK_IVR_FIELDS:
            val = item.get(key)
            if val is not None:
                raw = val
                break

    if raw is None:
        return None

    # Handle string vs float
    try:
        ivr = float(raw)
    except (TypeError, ValueError):
        return None

    # Heuristic: detect 0–100 vs 0–1 ranges.
    # - Typical Tasty field is already 0–1 (e.g. 0.2687).
    # - If it looks like a percent (e.g. 26.87), scale.
    if 1.5 < ivr <= 150.0:
        ivr = ivr / 100.0

    # Clamp to [0.0, 1.0]; hard clamp so out-of-range values become bounds
    if ivr < 0.0:
        ivr = 0.0
    elif ivr > 1.0:
        ivr = 1.0

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


def _iterate_market_metrics_chunks(
    symbols: Sequence[str],
    *,
    session: requests.Session,
    chunk_size: int,
) -> Iterable[tuple[List[str], Dict[str, Any]]]:
    """Yield (chunk, payload) for market-metrics requests with retries."""
    syms = sorted({(s or "").upper() for s in symbols})

    for i in range(0, len(syms), chunk_size):
        chunk = syms[i : i + chunk_size]
        if not chunk:
            continue

        params = {"symbols": ",".join(chunk)}

        attempts = 0
        payload: Optional[Dict[str, Any]] = None
        while attempts < MAX_RETRIES_PER_CHUNK:
            attempts += 1
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

            break  # success for this chunk; move to next

        if payload is None:
            continue

        yield chunk, payload


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

    if session is None:
        session = make_tasty_session_from_env()

    out: Dict[str, float] = {}

    for chunk, payload in _iterate_market_metrics_chunks(
        symbols, session=session, chunk_size=chunk_size
    ):
        chunk_hits = 0
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

    return out


def fetch_market_metrics_raw(
    symbols: Sequence[str],
    *,
    session: Optional[requests.Session] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Dict[str, Any]:
    """Fetch raw market-metrics payloads for the given symbols.

    Returns a dict with a ``data.items`` list combining all chunk responses.
    Intended for debugging IV Rank alignment against the tastytrade UI.
    """
    if not symbols:
        return {"data": {"items": []}}

    if session is None:
        session = make_tasty_session_from_env()

    items: List[Dict[str, Any]] = []
    for _chunk, payload in _iterate_market_metrics_chunks(
        symbols, session=session, chunk_size=chunk_size
    ):
        items.extend(_items_from_response(payload))

    return {"data": {"items": items}}
