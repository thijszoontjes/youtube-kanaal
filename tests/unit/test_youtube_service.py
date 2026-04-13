from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.config import load_settings
from youtube_kanaal.services.youtube_service import YouTubeService


def test_mock_auth_does_not_overwrite_existing_token(
    configured_env: dict[str, Path],
    monkeypatch,
) -> None:
    token_path = configured_env["data_dir"] / "credentials" / "youtube_token.json"
    original_token = (
        '{"client_id":"client","client_secret":"secret","refresh_token":"refresh","type":"authorized_user"}'
    )
    token_path.write_text(original_token, encoding="utf-8")

    monkeypatch.setenv("MOCK_MODE", "true")
    settings = load_settings()

    returned_path = YouTubeService(settings).authenticate(force=False)

    assert returned_path == token_path
    assert token_path.read_text(encoding="utf-8") == original_token


def test_mock_upload_video_supports_scheduled_publish_at(
    configured_env: dict[str, Path],
    monkeypatch,
) -> None:
    monkeypatch.setenv("MOCK_MODE", "true")
    settings = load_settings()
    service = YouTubeService(settings)
    response_path = configured_env["output_dir"] / "youtube_upload.json"
    publish_at = datetime.fromisoformat("2030-04-13T10:00:00+02:00")

    metadata = service.upload_video(
        video_path=configured_env["output_dir"] / "short.mp4",
        title="Scheduled short",
        description="desc",
        hashtags=["#Shorts"],
        privacy_status="private",
        scheduled_publish_at=publish_at,
        response_path=response_path,
    )

    payload = json.loads(response_path.read_text(encoding="utf-8"))

    assert metadata.scheduled_publish_at == publish_at
    assert metadata.privacy_status == "private"
    assert payload["status"]["privacyStatus"] == "private"
    assert payload["status"]["publishAt"] == "2030-04-13T08:00:00Z"


def test_mock_upload_video_rejects_scheduled_publish_when_not_private(
    configured_env: dict[str, Path],
    monkeypatch,
) -> None:
    monkeypatch.setenv("MOCK_MODE", "true")
    settings = load_settings()
    service = YouTubeService(settings)

    with pytest.raises(PipelineStageError):
        service.upload_video(
            video_path=configured_env["output_dir"] / "short.mp4",
            title="Scheduled short",
            description="desc",
            hashtags=["#Shorts"],
            privacy_status="public",
            scheduled_publish_at=datetime.fromisoformat("2030-04-13T10:00:00+02:00"),
            response_path=configured_env["output_dir"] / "youtube_upload.json",
        )


def test_authenticate_fails_with_actionable_error_for_incomplete_client_secret(
    configured_env: dict[str, Path],
    monkeypatch,
) -> None:
    monkeypatch.setenv("MOCK_MODE", "false")
    configured_env["client_secret_path"].write_text('{"installed":{"client_id":"mock"}}', encoding="utf-8")
    settings = load_settings()

    with pytest.raises(PipelineStageError) as exc_info:
        YouTubeService(settings).authenticate(force=False)

    assert exc_info.value.stage == "youtube_auth"
    assert "incomplete" in exc_info.value.message.lower()
    assert "desktop app json" in (exc_info.value.probable_cause or "").lower()
