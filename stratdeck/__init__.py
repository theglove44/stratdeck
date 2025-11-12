"""StratDeck package bootstrap.

We keep this module light, but it now loads environment overrides from a
project-root `.env` file so local credentials (e.g., tastytrade) are picked up
when using `python -m stratdeck.cli` without manually exporting vars.
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            # Remove surrounding quotes and inline comments if present
            if value and value[0] in {'"', "'"} and value[-1:] == value[0]:
                value = value[1:-1]
            else:
                hash_idx = value.find(" #")
                if hash_idx != -1:
                    value = value[:hash_idx].strip()
            os.environ.setdefault(key, value)
    except Exception as exc:
        # Failing to parse .env shouldn't crash the CLI; surface minimally.
        print(f"[stratdeck] warn: could not parse .env ({exc})")


_load_dotenv()
