from __future__ import annotations

import base64
from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.services.online_runtime_service import OnlineRuntimeService


def test_online_runtime_service_materializes_secret_files(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        youtube_client_secret_path=tmp_path / "data" / "credentials" / "client_secret.json",
        youtube_token_path=tmp_path / "data" / "credentials" / "youtube_token.json",
        xtts_speaker_wav_dir=tmp_path / "data" / "voice_samples" / "en",
    )
    monkeypatch.setenv("ONLINE_YOUTUBE_CLIENT_SECRET_JSON", '{"installed":{"client_id":"abc"}}')
    monkeypatch.setenv("ONLINE_YOUTUBE_TOKEN_JSON_B64", base64.b64encode(b'{"refresh_token":"xyz"}').decode("ascii"))
    monkeypatch.setenv("ONLINE_XTTS_REFERENCE_AUDIO_B64", base64.b64encode(b"m4a-binary").decode("ascii"))
    monkeypatch.setenv("ONLINE_XTTS_REFERENCE_AUDIO_FILENAME", "thijs.m4a")

    written = OnlineRuntimeService(settings).materialize_from_environment()

    assert settings.youtube_client_secret_path.read_text(encoding="utf-8") == '{"installed":{"client_id":"abc"}}'
    assert settings.youtube_token_path.read_text(encoding="utf-8") == '{"refresh_token":"xyz"}'
    assert (settings.xtts_speaker_wav_dir / "thijs.m4a").read_bytes() == b"m4a-binary"
    assert {item["kind"] for item in written} == {
        "youtube-client-secret",
        "youtube-token",
        "xtts-reference-audio",
    }
