from __future__ import annotations

import json
from pathlib import Path

from youtube_kanaal.cli import app


def test_cli_test_pipeline_and_validate_assets(cli_runner, configured_env) -> None:
    run_result = cli_runner.invoke(app, ["test-pipeline"])
    assert run_result.exit_code == 0, run_result.stdout
    assert "Short Completed" in run_result.stdout
    output_dir = Path(configured_env["output_dir"])
    latest_run_dir = max(output_dir.iterdir(), key=lambda path: path.stat().st_mtime)
    metadata = json.loads((latest_run_dir / "metadata" / "run_metadata.json").read_text(encoding="utf-8"))
    assert "sound_design" in metadata["stages"]

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


def test_cli_preview_voice_reports_fallback_reason_when_xtts_runtime_is_missing(
    cli_runner,
    configured_env,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NARRATION_ENGINE", "xtts")
    sample_dir = configured_env["data_dir"] / "voice_samples" / "en"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "memo.m4a").write_bytes(b"voice")
    monkeypatch.setattr(
        "youtube_kanaal.services.xtts_service.XTTSService.runtime_ready",
        lambda self: (False, "XTTS Docker image is not ready locally."),
    )

    preview_result = cli_runner.invoke(
        app,
        ["preview-voice", "Fallback to the default voice.", "--mock-mode"],
    )

    assert preview_result.exit_code == 0, preview_result.stdout
    normalized_output = " ".join(preview_result.stdout.lower().split())
    assert "using piper" in normalized_output
    assert "fallback reason" in normalized_output
    assert "docker image is not ready locally" in normalized_output


def test_cli_diagnose_voice_lists_reference_audio(cli_runner, configured_env, monkeypatch) -> None:
    monkeypatch.setenv("NARRATION_ENGINE", "xtts")
    sample_dir = configured_env["data_dir"] / "voice_samples" / "en"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "memo.m4a").write_bytes(b"voice")

    diagnose_result = cli_runner.invoke(app, ["diagnose-voice", "--mock-mode"])

    assert diagnose_result.exit_code == 0, diagnose_result.stdout
    assert "Voice Diagnostics" in diagnose_result.stdout
    assert "Reference Audio" in diagnose_result.stdout
    assert "memo.m4a" in diagnose_result.stdout


def test_cli_list_topics_shows_gaming_catalog(cli_runner, configured_env) -> None:
    result = cli_runner.invoke(app, ["list-topics", "--bucket", "gaming"])

    assert result.exit_code == 0, result.stdout
    assert "Topic Catalog" in result.stdout
    assert "gaming" in result.stdout
    assert "Fortnite" in result.stdout


def test_cli_make_short_accepts_fortnite_topic_in_mock_mode(cli_runner, configured_env) -> None:
    result = cli_runner.invoke(
        app,
        ["make-short", "--topic", "fortnite", "--mock-mode", "--no-downloads"],
    )

    assert result.exit_code == 0, result.stdout
    assert "Short Completed" in result.stdout
    assert "Fortnite" in result.stdout
