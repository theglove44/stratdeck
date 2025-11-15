from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

DEFAULT_IDEAS_PATH = Path(".stratdeck/last_trade_ideas.json")


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
