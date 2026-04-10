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
