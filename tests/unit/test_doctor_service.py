from __future__ import annotations

from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.services.doctor import DoctorService


def _base_settings(tmp_path: Path) -> Settings:
    downloads_dir = tmp_path / "downloads"
    cache_dir = tmp_path / "cache"
    data_dir = tmp_path / "data"
    credentials_dir = data_dir / "credentials"
    credentials_dir.mkdir(parents=True, exist_ok=True)
    (credentials_dir / "client_secret.json").write_text(
        (
            '{"installed":{"client_id":"mock-client","project_id":"mock-project",'
            '"auth_uri":"https://accounts.google.com/o/oauth2/auth",'
            '"token_uri":"https://oauth2.googleapis.com/token",'
            '"auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs",'
            '"client_secret":"mock-secret","redirect_uris":["http://localhost"]}}'
        ),
        encoding="utf-8",
    )
    return Settings(
        app_debug=False,
        narration_engine="xtts",
        xtts_runtime="docker",
        xtts_speaker_wav_dir=tmp_path / "voice_samples",
        xtts_speaker_wav_path=None,
        downloads_dir=downloads_dir,
        cache_dir=cache_dir,
        data_dir=data_dir,
        logs_dir=tmp_path / "logs",
        database_path=data_dir / "youtube_kanaal.db",
        youtube_client_secret_path=credentials_dir / "client_secret.json",
        youtube_token_path=credentials_dir / "youtube_token.json",
        pexels_api_key="mock-key",
        ffmpeg_binary="ffmpeg",
        piper_binary="piper",
        piper_voice_model_path=cache_dir / "piper" / "mock.onnx",
        whisper_cpp_binary="whisper-cli",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3.2:3b",
    )


def test_xtts_runtime_warns_when_image_missing_with_samples_and_piper_fallback(tmp_path, monkeypatch) -> None:
    settings = _base_settings(tmp_path)
    monkeypatch.setattr("youtube_kanaal.services.doctor.command_exists", lambda command: True)
    monkeypatch.setattr("youtube_kanaal.services.piper_service.command_exists", lambda command: True)
    monkeypatch.setattr(
        "youtube_kanaal.services.xtts_service.XTTSService.discover_reference_sources",
        lambda self, logger=None: [tmp_path / "voice_samples" / "sample.m4a"],
    )
    monkeypatch.setattr(
        "youtube_kanaal.services.xtts_service.XTTSService.runtime_ready",
        lambda self: (False, "XTTS Docker image is not ready locally."),
    )

    checks = DoctorService(settings)._xtts_checks()
    runtime_check = next(check for check in checks if check.name == "XTTS runtime")

    assert runtime_check.status == "warn"
    assert "fall back to Piper" in (runtime_check.action or "")


def test_xtts_runtime_warns_when_image_missing_but_piper_fallback_is_still_active(tmp_path, monkeypatch) -> None:
    settings = _base_settings(tmp_path)
    assert settings.piper_voice_model_path is not None
    piper_model_path = settings.piper_voice_model_path
    piper_model_path.parent.mkdir(parents=True, exist_ok=True)
    piper_model_path.write_bytes(b"mock")
    monkeypatch.setattr("youtube_kanaal.services.doctor.command_exists", lambda command: True)
    monkeypatch.setattr("youtube_kanaal.services.piper_service.command_exists", lambda command: True)
    monkeypatch.setattr(
        "youtube_kanaal.services.xtts_service.XTTSService.discover_reference_sources",
        lambda self, logger=None: [],
    )
    monkeypatch.setattr(
        "youtube_kanaal.services.xtts_service.XTTSService.runtime_ready",
        lambda self: (False, "XTTS Docker image is not ready locally."),
    )

    checks = DoctorService(settings)._xtts_checks()
    runtime_check = next(check for check in checks if check.name == "XTTS runtime")
    sample_check = next(check for check in checks if check.name == "XTTS speaker samples")

    assert runtime_check.status == "warn"
    assert "fall back to Piper" in (runtime_check.action or "")
    assert sample_check.status == "warn"


def test_youtube_oauth_check_fails_for_incomplete_client_secret_json(tmp_path) -> None:
    settings = _base_settings(tmp_path)
    settings.youtube_client_secret_path.write_text('{"installed":{"client_id":"mock"}}', encoding="utf-8")

    check = DoctorService(settings)._youtube_oauth_check()

    assert check.status == "fail"
    assert "client_secret" in (check.action or "")
