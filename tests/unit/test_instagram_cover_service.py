from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image

from youtube_kanaal.config import Settings
from youtube_kanaal.services.instagram_cover_service import InstagramCoverService


def test_generate_cover_creates_portrait_grid_safe_image(tmp_path: Path) -> None:
    service = InstagramCoverService(Settings(mock_mode=True))
    cover_path = service.generate_cover(
        title="WHAT LIES BENEATH THE DESERT'S SURFACE?",
        topic="the Silk Road",
        output_path=tmp_path / "instagram_cover.jpg",
    )

    assert cover_path.exists()
    with Image.open(cover_path) as image:
        assert image.size == (1080, 1920)


def test_build_reel_with_cover_prepends_cover_with_ffmpeg(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run_command(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"video")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("youtube_kanaal.services.instagram_cover_service.run_command", fake_run_command)
    service = InstagramCoverService(Settings(mock_mode=False, ffmpeg_binary="ffmpeg"))
    video_path = tmp_path / "source.mp4"
    cover_path = tmp_path / "cover.jpg"
    output_path = tmp_path / "with-cover.mp4"
    video_path.write_bytes(b"video")
    cover_path.write_bytes(b"cover")

    result = service.build_reel_with_cover(
        video_path=video_path,
        cover_path=cover_path,
        output_path=output_path,
    )

    assert result == output_path
    assert output_path.exists()
    command_text = " ".join(commands[0])
    assert "concat=n=2:v=1:a=0" in command_text
    assert str(cover_path) in commands[0]
