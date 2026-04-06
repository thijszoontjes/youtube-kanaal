from __future__ import annotations

from pathlib import Path

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
