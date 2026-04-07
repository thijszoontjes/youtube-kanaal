from __future__ import annotations

from pathlib import Path

from youtube_kanaal.cli import app


def test_cli_test_pipeline_and_validate_assets(cli_runner, configured_env) -> None:
    run_result = cli_runner.invoke(app, ["test-pipeline"])
    assert run_result.exit_code == 0, run_result.stdout
    assert "Short Completed" in run_result.stdout

    validate_result = cli_runner.invoke(app, ["validate-assets"])
    assert validate_result.exit_code == 0, validate_result.stdout
    assert "Artifacts valid" in validate_result.stdout


def test_cli_preview_voice_with_xtts_mock(cli_runner, configured_env, monkeypatch) -> None:
    monkeypatch.setenv("NARRATION_ENGINE", "xtts")

    preview_result = cli_runner.invoke(
        app,
        ["preview-voice", "This is my cloned English voice.", "--mock-mode"],
    )

    assert preview_result.exit_code == 0, preview_result.stdout
    preview_path = Path(configured_env["output_dir"]) / "voice-preview" / "preview.wav"
    assert preview_path.exists()
