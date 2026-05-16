from __future__ import annotations

from pathlib import Path

import pytest

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.services.ffmpeg_service import FFmpegService
from youtube_kanaal.services.xtts_service import XTTSService


def test_ffprobe_binary_uses_exe_suffix_when_ffmpeg_path_is_windows_executable(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_path = bin_dir / "ffmpeg.exe"
    ffmpeg_path.write_bytes(b"")

    settings = Settings(ffmpeg_binary=str(ffmpeg_path))

    assert FFmpegService(settings)._ffprobe_binary().endswith("ffprobe.exe")
    assert XTTSService(settings)._ffprobe_binary().endswith("ffprobe.exe")


def test_validate_long_video_allows_minor_encoder_duration_rounding(monkeypatch: pytest.MonkeyPatch) -> None:
    service = FFmpegService(Settings())

    monkeypatch.setattr(
        service,
        "_probe_video",
        lambda *_args, **_kwargs: {
            "streams": [{"width": 1280, "height": 720, "duration": "509.87"}],
            "format": {"duration": "509.87"},
        },
    )

    payload = service.validate_long_video(Path("rendered.mp4"), min_seconds=510, max_seconds=660)

    assert payload["streams"][0]["duration"] == "509.87"


def test_validate_long_video_rejects_materially_short_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    service = FFmpegService(Settings())

    monkeypatch.setattr(
        service,
        "_probe_video",
        lambda *_args, **_kwargs: {
            "streams": [{"width": 1280, "height": 720, "duration": "509.40"}],
            "format": {"duration": "509.40"},
        },
    )

    with pytest.raises(PipelineStageError, match="outside 510-660s"):
        service.validate_long_video(Path("rendered.mp4"), min_seconds=510, max_seconds=660)
