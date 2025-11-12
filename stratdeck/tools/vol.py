import json
import os
from typing import Dict

def load_snapshot(path: str = None) -> Dict[str, float]:
    """
    Load IV/IVR snapshot as {SYMBOL: ivr_float_0to1}.
    Falls back to sane defaults if file not found.
    """
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "..", "data", "iv_snapshot.json")
    try:
        with open(path, "r") as f:
            raw = json.load(f)
        # accept either {sym:{ivr:0.42}} or {sym:0.42}
        out = {}
        for k, v in raw.items():
            out[k] = float(v["ivr"]) if isinstance(v, dict) and "ivr" in v else float(v)
        return out
    except FileNotFoundError:
        # fallback so MVP runs day one
        return {"SPX": 0.35, "XSP": 0.38, "QQQ": 0.29, "IWM": 0.33}