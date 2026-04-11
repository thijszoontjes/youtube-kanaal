from __future__ import annotations

import base64
import json
from datetime import datetime
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


def test_cli_prepare_online_runtime_writes_files(cli_runner, configured_env, monkeypatch) -> None:
    monkeypatch.setenv("ONLINE_YOUTUBE_CLIENT_SECRET_JSON", '{"installed":{"client_id":"mock"}}')
    monkeypatch.setenv(
        "ONLINE_YOUTUBE_TOKEN_JSON_B64",
        base64.b64encode(b'{"refresh_token":"mock"}').decode("ascii"),
    )
    monkeypatch.setenv("ONLINE_XTTS_REFERENCE_AUDIO_B64", base64.b64encode(b"voice").decode("ascii"))
    monkeypatch.setenv("ONLINE_XTTS_REFERENCE_AUDIO_FILENAME", "memo.m4a")

    result = cli_runner.invoke(app, ["prepare-online-runtime"])

    assert result.exit_code == 0, result.stdout
    assert "Online Runtime Prepared" in result.stdout
    assert configured_env["client_secret_path"].exists()
    assert (configured_env["data_dir"] / "voice_samples" / "en" / "memo.m4a").exists()


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


def test_cli_make_short_upload_skips_downloads_copy(cli_runner, configured_env) -> None:
    result = cli_runner.invoke(
        app,
        ["make-short", "--upload", "--mock-mode"],
    )

    assert result.exit_code == 0, result.stdout
    assert "Short Completed" in result.stdout
    assert "not copied" in result.stdout

    output_dir = Path(configured_env["output_dir"])
    latest_run_dir = max(output_dir.iterdir(), key=lambda path: path.stat().st_mtime)
    metadata = json.loads((latest_run_dir / "metadata" / "run_metadata.json").read_text(encoding="utf-8"))
    assert metadata["upload"]["uploaded"] is True
    assert metadata["validation"]
    assert metadata["stages"]["downloads_export"]["copied"] is False


def test_cli_make_short_schedule_creates_four_scheduled_uploads(cli_runner, configured_env) -> None:
    result = cli_runner.invoke(
        app,
        [
            "make-short-schedule",
            "--date",
            "2026-04-12",
            "--times",
            "10:00,13:00,15:00,19:00",
            "--mock-mode",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Scheduled Uploads" in result.stdout

    output_dir = Path(configured_env["output_dir"])
    run_dirs = sorted(
        [path for path in output_dir.iterdir() if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
    )
    assert len(run_dirs) == 4

    scheduled_hours: list[int] = []
    for run_dir in run_dirs:
        metadata = json.loads((run_dir / "metadata" / "run_metadata.json").read_text(encoding="utf-8"))
        assert metadata["upload"]["uploaded"] is True
        assert metadata["upload"]["privacy_status"] == "private"
        assert metadata["stages"]["downloads_export"]["copied"] is False
        scheduled_publish_at = metadata["upload"]["scheduled_publish_at"]
        assert scheduled_publish_at is not None
        scheduled_hours.append(datetime.fromisoformat(scheduled_publish_at).hour)

    assert scheduled_hours == [10, 13, 15, 19]
