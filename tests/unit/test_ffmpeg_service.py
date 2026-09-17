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


def test_validate_long_video_accepts_explicit_preview_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    service = FFmpegService(Settings())

    monkeypatch.setattr(
        service,
        "_probe_video",
        lambda *_args, **_kwargs: {
            "streams": [{"width": 1280, "height": 720, "duration": "60.00"}],
            "format": {"duration": "60.00"},
        },
    )

    payload = service.validate_long_video(
        Path("rendered.mp4"),
        min_seconds=60,
        max_seconds=60,
        expected_width=1280,
        expected_height=720,
    )

    assert payload["streams"][0]["width"] == 1280


def test_long_photo_filter_forces_smooth_constant_30fps() -> None:
    service = FFmpegService(Settings())

    filter_graph = service._long_photo_filter(
        duration_seconds=5,
        variant=1,
        title_path=Path("title.txt"),
        caption_path=Path("caption.txt"),
        width=1280,
        height=720,
    )

    assert "fps=30" in filter_graph
    assert "settb=AVTB" in filter_graph


def test_long_photo_filter_can_hold_a_static_evidence_card() -> None:
    service = FFmpegService(Settings())

    filter_graph = service._long_photo_filter(
        duration_seconds=5,
        variant=1,
        title_path=Path("title.txt"),
        caption_path=Path("caption.txt"),
        width=1280,
        height=720,
        motion=False,
    )

    assert "sin(t*0.35)" not in filter_graph
    assert "cos(t*0.28)" not in filter_graph


def test_long_photo_filter_leaves_subtitles_to_the_timed_ass_track() -> None:
    service = FFmpegService(Settings())

    filter_graph = service._long_photo_filter(
        duration_seconds=5,
        variant=1,
        title_path=Path("title.txt"),
        caption_path=Path("caption.txt"),
        width=1280,
        height=720,
    )

    assert "caption.txt" not in filter_graph
    assert "drawtext" in filter_graph


def test_long_photo_filter_reveals_a_new_topic_with_smooth_motion() -> None:
    service = FFmpegService(Settings())

    filter_graph = service._long_photo_filter(
        duration_seconds=5,
        variant=1,
        title_path=Path("title.txt"),
        caption_path=Path("caption.txt"),
        width=1280,
        height=720,
        reveal=True,
    )

    assert "cos(t*1.8)" in filter_graph
    assert "scale=614:425" in filter_graph


def test_extract_frame_uses_requested_timestamp_and_high_quality_jpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_command(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs

    monkeypatch.setattr("youtube_kanaal.services.ffmpeg_service.run_command", fake_run_command)
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"not-a-placeholder")
    output_path = tmp_path / "first-frame.jpg"

    FFmpegService(Settings()).extract_frame(
        video_path=video_path,
        output_path=output_path,
        timestamp_seconds=0.0,
    )

    command = captured["command"]
    assert command[command.index("-ss") + 1] == "0.00"
    assert command[command.index("-frames:v") + 1] == "1"
    assert command[command.index("-q:v") + 1] == "2"


def test_long_photo_variants_change_the_visual_card_size() -> None:
    service = FFmpegService(Settings())
    primary = service._long_photo_filter(
        duration_seconds=6.0,
        variant=1,
        title_path=Path("title.txt"),
        caption_path=Path("caption.txt"),
        width=1920,
        height=1080,
        visual_variant="primary",
    )
    punch = service._long_photo_filter(
        duration_seconds=6.0,
        variant=2,
        title_path=Path("title.txt"),
        caption_path=Path("caption.txt"),
        width=1920,
        height=1080,
        visual_variant="punch",
    )

    assert primary != punch
    assert "scale=827:572" in primary
    assert "scale=1140:801" in punch
