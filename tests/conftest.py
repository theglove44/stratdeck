# tests/conftest.py
import sys
from pathlib import Path

# Ensure project root (the directory that contains 'stratdeck') is on sys.path
ROOT = Path(__file__).resolve().parent.parent  # one level up from tests/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
