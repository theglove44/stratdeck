from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional, Union

DateLike = Union[str, date, datetime, None]


def compute_dte(expiry: DateLike) -> Optional[int]:
    """
    Compute calendar days to expiration from an ISO-like string/date/datetime.

    Returns None when the input cannot be parsed; clamps negative values to 0.
    """
    if expiry is None:
        return None

    if isinstance(expiry, datetime):
        exp_date = expiry.date()
    elif isinstance(expiry, date):
        exp_date = expiry
    else:
        try:
            exp_date = datetime.fromisoformat(str(expiry)).date()
        except Exception:
            return None

    today = datetime.now(timezone.utc).date()
    return max((exp_date - today).days, 0)
