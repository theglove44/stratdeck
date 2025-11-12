import os
import yaml

_CONF_CACHE = {}

def load_yaml(path: str):
    if path in _CONF_CACHE:
        return _CONF_CACHE[path]
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing config file: {path}")
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    _CONF_CACHE[path] = data
    return data

def cfg():
    # main app config
    return load_yaml(os.path.join(os.path.dirname(__file__), "..", "conf", "stratdeck.yml"))

def scoring_conf():
    return load_yaml(os.path.join(os.path.dirname(__file__), "..", "conf", "scoring.yml"))