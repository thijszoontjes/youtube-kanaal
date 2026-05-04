from __future__ import annotations

from pathlib import Path

from youtube_kanaal.config import Settings
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


def test_hook_text_filter_adds_short_intro_overlay() -> None:
    service = FFmpegService(Settings())

    hook_filter = service._hook_text_filter("This should not exist underwater")

    assert hook_filter is not None
    assert hook_filter.startswith("drawtext=")
    assert "between(t,0,1.5)" in hook_filter
    assert "This should not\\\\nexist underwater" in hook_filter
