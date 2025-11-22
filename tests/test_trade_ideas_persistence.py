import json
import os
from pathlib import Path

from click.testing import CliRunner

from stratdeck import cli


def test_trade_ideas_writes_last_file():
    runner = CliRunner()
    env = os.environ.copy()
    env["STRATDECK_DATA_MODE"] = "mock"

    result = runner.invoke(
        cli.cli,
        [
            "trade-ideas",
            "--universe",
            "index_core",
            "--strategy",
            "short_put_spread_index_45d",
            "--json-output",
        ],
        env=env,
    )

    assert result.exit_code == 0, result.output

    lines = result.output.splitlines()
    try:
        start_idx = next(i for i, line in enumerate(lines) if line.strip() == "[")
        end_idx = len(lines) - 1 - next(
            i for i, line in enumerate(reversed(lines)) if line.strip() == "]"
        )
    except StopIteration:
        assert False, result.output

    payload_text = "\n".join(lines[start_idx : end_idx + 1])
    ideas_stdout = json.loads(payload_text)
    assert isinstance(ideas_stdout, list)

    last_path = Path(".stratdeck/last_trade_ideas.json")
    assert last_path.exists()

    ideas_file = json.loads(last_path.read_text())

    assert ideas_file == ideas_stdout
    if ideas_stdout:
        sample = ideas_stdout[0]
        assert "strategy_id" in sample
        assert "universe_id" in sample
        assert "filters_passed" in sample
