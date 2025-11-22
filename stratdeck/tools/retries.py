from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterable, Optional, Sequence

log = logging.getLogger(__name__)


def _status_from_exception(exc: BaseException) -> Optional[int]:
    """
    Best-effort extraction of an HTTP-like status code from an exception.

    This looks at common attributes (status_code, status) and any attached
    response objects to decide whether a failure is retryable (e.g. 429).
    """
    for attr in ("status_code", "status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val

    resp = getattr(exc, "response", None)
    if resp is not None:
        for attr in ("status_code", "status"):
            val = getattr(resp, attr, None)
            if isinstance(val, int):
                return val
    return None


def _is_retryable_error(exc: BaseException, retry_statuses: Sequence[int]) -> bool:
    status = _status_from_exception(exc)
    if status is not None and status in retry_statuses:
        return True

    msg = str(exc).lower()
    if "too many requests" in msg or "rate limit" in msg or "429" in msg:
        return True

    retry_types: Iterable[type] = (ConnectionError, TimeoutError)
    return isinstance(exc, retry_types)


def call_with_retries(
    fn: Callable[[], Any],
    *,
    retries: int = 2,
    backoff: float = 0.5,
    retry_statuses: Sequence[int] = (429, 500, 502, 503, 504),
    logger: Optional[logging.Logger] = None,
    label: str = "call",
) -> Any:
    """
    Execute `fn` with bounded retries for transient errors (e.g. HTTP 429).

    Retries use exponential backoff starting at `backoff`. Non-retryable errors
    are raised immediately so callers can handle or fail fast.
    """
    lg = logger or log
    attempt = 0
    delay = backoff
    while True:
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - surfaced via decision tree
            attempt += 1
            retryable = _is_retryable_error(exc, retry_statuses)
            if not retryable:
                raise
            if attempt > retries:
                lg.warning(
                    "[retry] %s exhausted after %s attempts: %r",
                    label,
                    attempt,
                    exc,
                )
                raise
            lg.warning(
                "[retry] %s attempt %s/%s failed (%r); backing off %.2fs",
                label,
                attempt,
                retries,
                exc,
                delay,
            )
            time.sleep(delay)
            delay *= 2
