from __future__ import annotations

import pytest
from pydantic import ValidationError

from youtube_kanaal.config import Settings


def test_settings_reject_invalid_duration_window() -> None:
    with pytest.raises(ValidationError):
        Settings(
            min_short_duration_seconds=40,
            max_short_duration_seconds=20,
        )


def test_settings_expand_downloads_path(tmp_path) -> None:
    settings = Settings(app_debug=False, downloads_dir=str(tmp_path / "downloads"))
    assert settings.downloads_dir.name == "downloads"
