from __future__ import annotations

import pytest
from pydantic import ValidationError

from youtube_kanaal.config import Settings


def test_settings_default_to_kokoro_narration() -> None:
    settings = Settings(_env_file=None)

    assert settings.narration_engine == "kokoro"
    assert settings.kokoro_voice == "af_heart"
    assert settings.kokoro_speed == 1.05


def test_settings_default_to_fast_local_ollama_model_and_longer_timeout() -> None:
    settings = Settings(_env_file=None)

    assert settings.ollama_model == "llama3.2:3b"
    assert settings.ollama_keep_alive == "15m"
    assert settings.ollama_context_length == 4096
    assert settings.ollama_long_context_length == 8192
    assert settings.ollama_long_max_output_tokens == 4608
    assert settings.ollama_temperature == 0.2
    assert settings.ollama_timeout_seconds == 900


def test_settings_reject_invalid_duration_window() -> None:
    with pytest.raises(ValidationError):
        Settings(
            min_short_duration_seconds=40,
            max_short_duration_seconds=20,
        )


def test_settings_expand_downloads_path(tmp_path) -> None:
    settings = Settings(app_debug=False, downloads_dir=str(tmp_path / "downloads"))
    assert settings.downloads_dir.name == "downloads"


def test_settings_reject_invalid_schedule_times() -> None:
    with pytest.raises(ValidationError):
        Settings(scheduled_run_times="13:00,99:00")


def test_settings_reject_invalid_narration_engine() -> None:
    with pytest.raises(ValidationError):
        Settings(narration_engine="clone")


def test_settings_accept_chatterbox_narration() -> None:
    settings = Settings(
        narration_engine="chatterbox",
        chatterbox_model="turbo",
        chatterbox_device="auto",
    )

    assert settings.narration_engine == "chatterbox"
    assert settings.chatterbox_model == "turbo"
    assert settings.chatterbox_device == "auto"


def test_settings_reject_invalid_chatterbox_model() -> None:
    with pytest.raises(ValidationError):
        Settings(chatterbox_model="large")


def test_settings_reject_invalid_xtts_reference_max_seconds() -> None:
    with pytest.raises(ValidationError):
        Settings(xtts_reference_max_seconds=2)
