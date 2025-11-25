import json

import stratdeck.tools.build_iv_snapshot as builder
from stratdeck.tools import vol


def test_build_iv_snapshot_writes_nested_structure(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "resolve_live_universe_symbols", lambda: {"SPX", "AAPL"})
    monkeypatch.setattr(
        builder, "fetch_iv_rank_for_symbols", lambda symbols: {"SPX": 0.32, "AAPL": 0.45}
    )
    path = tmp_path / "iv_snapshot.json"

    snapshot = builder.build_iv_snapshot(path)

    on_disk = json.loads(path.read_text())
    expected = {"AAPL": {"ivr": 0.45}, "SPX": {"ivr": 0.32}}
    assert snapshot == expected
    assert on_disk == expected


def test_build_iv_snapshot_round_trip_with_load_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "resolve_live_universe_symbols", lambda: {"SPX"})
    monkeypatch.setattr(builder, "fetch_iv_rank_for_symbols", lambda symbols: {"SPX": 0.27})
    path = tmp_path / "iv_snapshot.json"

    builder.build_iv_snapshot(path)
    loaded = vol.load_snapshot(str(path))

    assert loaded == {"SPX": 0.27}


def test_build_iv_snapshot_handles_empty_universe(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "resolve_live_universe_symbols", lambda: set())
    path = tmp_path / "iv_snapshot.json"

    snapshot = builder.build_iv_snapshot(path)
    assert snapshot == {}
    assert json.loads(path.read_text()) == {}
    assert vol.load_snapshot(str(path)) == {}
