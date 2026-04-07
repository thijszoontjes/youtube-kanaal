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
    sample_dir = configured_env["data_dir"] / "voice_samples" / "en"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "memo.m4a").write_bytes(b"voice")

    preview_result = cli_runner.invoke(
        app,
        ["preview-voice", "This is my cloned English voice.", "--mock-mode"],
    )

    assert preview_result.exit_code == 0, preview_result.stdout
    normalized_output = " ".join(preview_result.stdout.lower().split())
    assert "using xtts" in normalized_output
    preview_path = Path(configured_env["output_dir"]) / "voice-preview" / "preview.wav"
    assert preview_path.exists()


def test_cli_preview_voice_with_xtts_missing_samples_falls_back_to_piper(cli_runner, configured_env, monkeypatch) -> None:
    monkeypatch.setenv("NARRATION_ENGINE", "xtts")

    preview_result = cli_runner.invoke(
        app,
        ["preview-voice", "Fallback to the default voice.", "--mock-mode"],
    )

    assert preview_result.exit_code == 0, preview_result.stdout
    normalized_output = " ".join(preview_result.stdout.lower().split())
    assert "using piper" in normalized_output
