from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable, List, Sequence

DEFAULT_IDEAS_PATH = Path(".stratdeck/last_trade_ideas.json")
DISABLE_PERSIST_ENV = "STRATDECK_DISABLE_LAST_TRADE_IDEAS_FILE"

log = logging.getLogger(__name__)


def load_last_ideas(path: Path = DEFAULT_IDEAS_PATH) -> List[Any]:
    """
    Load the last TradeIdeas JSON produced by:
      python -m stratdeck.cli trade-ideas --json-output .stratdeck/last_trade_ideas.json

    Handles either:
      - {"ideas": [...]} style payloads
      - or a bare list: [...]
    """
    if not path.exists():
        raise FileNotFoundError(f"No ideas file at {path}; run 'trade-ideas --json-output {path}' first.")

    data = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(data, dict):
        for key in ("ideas", "results", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        # Fallback – wrap the dict itself
        return [data]

    if isinstance(data, list):
        return data

    return [data]


def persist_last_ideas(
    ideas: Sequence[Any] | Iterable[Any],
    path: Path = DEFAULT_IDEAS_PATH,
) -> bool:
    """
    Persist the last generated ideas to disk.

    Returns True on success, False on failure. Failures are logged but do not
    raise to keep CLI flows resilient.
    """
    if os.getenv(DISABLE_PERSIST_ENV) == "1":
        return False

    payload = list(ideas) if not isinstance(ideas, list) else ideas
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(payload, indent=2, default=str)
        path.write_text(blob, encoding="utf-8")
        return True
    except Exception as exc:  # pragma: no cover - defensive guard
        log.warning(
            "[ideas] failed to persist last trade ideas to %s: %s",
            path,
            exc,
        )
        return False
