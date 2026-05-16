from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.services.instagram_service import InstagramService


def test_instagram_service_requires_configuration() -> None:
    service = InstagramService(Settings(instagram_user_id=None, instagram_access_token=None))

    with pytest.raises(PipelineStageError, match="not configured") as exc_info:
        service.ensure_configured()

    assert "INSTAGRAM_USER_ID" in (exc_info.value.probable_cause or "")
    assert "INSTAGRAM_ACCESS_TOKEN" in (exc_info.value.probable_cause or "")


def test_instagram_mock_upload_writes_response(tmp_path: Path) -> None:
    video_path = tmp_path / "reel.mp4"
    video_path.write_bytes(b"video")
    response_path = tmp_path / "instagram.json"
    service = InstagramService(Settings(mock_mode=True))

    metadata = service.upload_reel(video_path=video_path, caption="test", response_path=response_path)

    assert metadata.uploaded is True
    assert metadata.instagram_media_id == "mock-instagram-media-id"
    assert response_path.exists()


def test_instagram_permission_error_is_actionable() -> None:
    response = httpx.Response(
        403,
        json={
            "error": {
                "message": "(#200) The user has not authorized this application",
                "code": 200,
                "fbtrace_id": "trace",
            }
        },
        request=httpx.Request("POST", "https://graph.facebook.com/v24.0/123/media"),
    )
    service = InstagramService(Settings(instagram_user_id="123", instagram_access_token="token"))

    with pytest.raises(PipelineStageError) as exc_info:
        service._raise_api_error(response, default_message="Instagram Graph API request failed.")

    assert exc_info.value.stage == "instagram_upload"
    assert "Content Publishing permission" in (exc_info.value.probable_cause or "")
    assert "fbtrace_id=trace" in (exc_info.value.probable_cause or "")
