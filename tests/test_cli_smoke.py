import os

from click.testing import CliRunner

from stratdeck import cli


def _mock_env():
    env = os.environ.copy()
    env["STRATDECK_DATA_MODE"] = "mock"
    return env


def test_doctor_smoke():
    runner = CliRunner()
    result = runner.invoke(cli.cli, ["doctor"], env=_mock_env())
    assert result.exit_code == 0, result.output
    assert "All green" in result.output


def test_trade_ideas_smoke():
    runner = CliRunner()
    result = runner.invoke(
        cli.cli,
        ["trade-ideas", "--universe", "index_core", "--json-output"],
        env=_mock_env(),
    )
    assert result.exit_code == 0, result.output
    assert result.exception is None
