import json

import pytest

from stratdeck.tools.vol import load_snapshot


def test_load_snapshot_handles_both_formats(tmp_path):
    path = tmp_path / "iv_snapshot.json"

    data_a = {
        "SPX": {"ivr": 0.15},
        "AAPL": {"ivr": 0.07},
    }
    path.write_text(json.dumps(data_a))

    snapshot = load_snapshot(path=str(path))
    assert snapshot["SPX"] == pytest.approx(0.15)
    assert snapshot["AAPL"] == pytest.approx(0.07)

    data_b = {
        "SPX": 0.15,
        "AAPL": 0.07,
    }
    path.write_text(json.dumps(data_b))

    snapshot = load_snapshot(path=str(path))
    assert snapshot["SPX"] == pytest.approx(0.15)
    assert snapshot["AAPL"] == pytest.approx(0.07)
