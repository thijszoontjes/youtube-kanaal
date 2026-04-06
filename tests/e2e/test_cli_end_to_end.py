from __future__ import annotations

from youtube_kanaal.cli import app


def test_cli_test_pipeline_and_validate_assets(cli_runner, configured_env) -> None:
    run_result = cli_runner.invoke(app, ["test-pipeline"])
    assert run_result.exit_code == 0, run_result.stdout
    assert "Short Completed" in run_result.stdout

    validate_result = cli_runner.invoke(app, ["validate-assets"])
    assert validate_result.exit_code == 0, validate_result.stdout
    assert "Artifacts valid" in validate_result.stdout
