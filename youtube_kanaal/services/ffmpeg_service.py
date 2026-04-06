from __future__ import annotations

import json
import shutil
import wave
from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.models.assets import AssetPlan
from youtube_kanaal.utils.files import write_text
from youtube_kanaal.utils.process import command_exists, run_command


class FFmpegService:
    """FFmpeg wrapper for audio normalization, rendering, and validation."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def normalize_audio(self, *, input_path: Path, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.mock_mode:
            shutil.copy2(input_path, output_path)
            return output_path

        filter_graph = (
            "silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB,"
            "areverse,"
            "silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB,"
            "areverse,"
            "loudnorm=I=-16:TP=-1.5:LRA=11"
        )
        run_command(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-i",
                str(input_path),
                "-af",
                filter_graph,
                "-ar",
                "48000",
                "-ac",
                "1",
                str(output_path),
            ],
            timeout_seconds=300,
            stage="narration_generation",
        )
        return output_path

    def audio_duration_seconds(self, audio_path: Path) -> float:
        with wave.open(str(audio_path), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            return round(frames / float(rate), 2)

    def render_short(
        self,
        *,
        plan: AssetPlan,
        audio_path: Path,
        subtitle_path: Path,
        working_dir: Path,
        output_path: Path,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.mock_mode:
            return self._render_mock_short(
                audio_path=audio_path,
                subtitle_path=subtitle_path,
                output_path=output_path,
            )

        segments_dir = working_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)
        segment_paths: list[Path] = []
        for index, segment in enumerate(plan.segments, start=1):
            segment_path = segments_dir / f"segment-{index:02d}.mp4"
            run_command(
                [
                    self.settings.ffmpeg_binary,
                    "-y",
                    "-ss",
                    "0",
                    "-t",
                    f"{segment.duration_seconds:.2f}",
                    "-i",
                    str(segment.clip_path),
                    "-vf",
                    "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "23",
                    str(segment_path),
                ],
                timeout_seconds=600,
                stage="video_rendering",
            )
            segment_paths.append(segment_path)

        concat_file = working_dir / "concat.txt"
        concat_lines = []
        for path in segment_paths:
            resolved_path = str(path.resolve()).replace("\\", "/")
            concat_lines.append(f"file '{resolved_path}'")
        write_text(
            concat_file,
            "\n".join(concat_lines) + "\n",
        )

        rough_cut_path = working_dir / "rough_cut.mp4"
        run_command(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-i",
                str(audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "22",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(rough_cut_path),
            ],
            timeout_seconds=600,
            stage="video_rendering",
        )

        style = (
            f"FontName={self.settings.subtitle_font_name},"
            f"FontSize={self.settings.subtitle_font_size},"
            f"Outline={self.settings.subtitle_outline},"
            f"MarginV={self.settings.subtitle_margin_v},"
            "Alignment=2,PrimaryColour=&H00FFFFFF,BackColour=&H80000000"
        )
        subtitle_filter = (
            f"subtitles={self._escape_filter_path(subtitle_path)}:force_style='{style}'"
        )
        run_command(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-i",
                str(rough_cut_path),
                "-vf",
                subtitle_filter,
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "22",
                "-c:a",
                "copy",
                str(output_path),
            ],
            timeout_seconds=600,
            stage="video_rendering",
        )
        return output_path

    def validate_video(self, video_path: Path) -> dict[str, object]:
        if not video_path.exists() or video_path.stat().st_size == 0:
            raise PipelineStageError(
                stage="validation",
                message="Rendered video was not created or is empty.",
                probable_cause="FFmpeg failed before producing the final MP4.",
            )
        if self._is_placeholder_video(video_path):
            return {
                "path": str(video_path),
                "size": video_path.stat().st_size,
                "mock_placeholder": True,
            }

        ffprobe_binary = self._ffprobe_binary()
        if not command_exists(ffprobe_binary):
            return {"path": str(video_path), "size": video_path.stat().st_size}
        result = run_command(
            [
                ffprobe_binary,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,duration",
                "-of",
                "json",
                str(video_path),
            ],
            timeout_seconds=60,
            stage="validation",
        )
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams", [])
        if not streams:
            raise PipelineStageError(
                stage="validation",
                message="ffprobe could not read the rendered video stream.",
                probable_cause="The MP4 file may be corrupt.",
            )
        stream = streams[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        if width != 1080 or height != 1920:
            raise PipelineStageError(
                stage="validation",
                message=f"Rendered video has unexpected dimensions {width}x{height}.",
                probable_cause="The FFmpeg scaling and crop filter did not produce a Shorts frame.",
            )
        return payload

    def _ffprobe_binary(self) -> str:
        ffmpeg_path = Path(self.settings.ffmpeg_binary)
        if ffmpeg_path.exists():
            return str(ffmpeg_path.with_name("ffprobe"))
        return "ffprobe"

    def _escape_filter_path(self, path: Path) -> str:
        normalized = str(path.resolve()).replace("\\", "/")
        return normalized.replace(":", "\\:")

    def _render_mock_short(
        self,
        *,
        audio_path: Path,
        subtitle_path: Path,
        output_path: Path,
    ) -> Path:
        if command_exists(self.settings.ffmpeg_binary):
            duration_seconds = self.audio_duration_seconds(audio_path)
            subtitle_filter = (
                f"subtitles={self._escape_filter_path(subtitle_path)}:"
                f"force_style='FontName={self.settings.subtitle_font_name},"
                f"FontSize={self.settings.subtitle_font_size},"
                f"Outline={self.settings.subtitle_outline},"
                f"MarginV={self.settings.subtitle_margin_v},"
                "Alignment=2,PrimaryColour=&H00FFFFFF,BackColour=&H80000000'"
            )
            run_command(
                [
                    self.settings.ffmpeg_binary,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=black:s=1080x1920:r=30:d={duration_seconds:.2f}",
                    "-i",
                    str(audio_path),
                    "-vf",
                    subtitle_filter,
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-preset",
                    "veryfast",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-shortest",
                    str(output_path),
                ],
                timeout_seconds=300,
                stage="video_rendering",
            )
            return output_path

        if self.settings.allow_placeholder_video:
            output_path.write_bytes(b"FAKE_MP4")
            return output_path

        raise PipelineStageError(
            stage="video_rendering",
            message="Cannot create a playable MP4 because FFmpeg is not installed.",
            probable_cause=(
                "This run used mock mode. The old placeholder MP4 behavior is disabled to avoid "
                "broken files in Downloads. Install FFmpeg and rerun."
            ),
        )

    def _is_placeholder_video(self, video_path: Path) -> bool:
        try:
            return video_path.read_bytes() == b"FAKE_MP4"
        except OSError:
            return False
