import os
from pathlib import Path

from stratdeck.orchestrator import run_open_cycle
from stratdeck.tools.positions import PaperPosition


def test_open_cycle_mock_mode_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")

    temp_pos_path = tmp_path / "positions.json"
    monkeypatch.setattr("stratdeck.tools.positions.POS_PATH", temp_pos_path)
    monkeypatch.setattr("stratdeck.orchestrator.POS_PATH", temp_pos_path)

    result = run_open_cycle(
        universe="index_core",
        strategy="short_put_spread_index_45d",
        max_trades=1,
        min_score=0,
    )

    assert result.generated_count >= 0
    assert len(result.opened) <= 1

    for opened in result.opened:
        assert opened.idea.symbol
        assert isinstance(opened.vetting.score, (int, float))
        assert isinstance(opened.position, PaperPosition)
