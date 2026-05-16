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


LONG_VIDEO_DURATION_TOLERANCE_SECONDS = 0.5


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

    def fit_audio_duration(
        self,
        *,
        input_path: Path,
        output_path: Path,
        current_duration_seconds: float,
        min_seconds: int,
        max_seconds: int,
    ) -> tuple[Path, float]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if min_seconds <= current_duration_seconds <= max_seconds:
            shutil.copy2(input_path, output_path)
            return output_path, current_duration_seconds

        target_duration = min(max(current_duration_seconds, min_seconds + 8), max_seconds - 8)
        if current_duration_seconds < min_seconds:
            target_duration = min_seconds + 8
        elif current_duration_seconds > max_seconds:
            target_duration = max_seconds - 8
        tempo = max(0.5, min(current_duration_seconds / target_duration, 2.0))
        if self.settings.mock_mode:
            shutil.copy2(input_path, output_path)
            return output_path, current_duration_seconds
        run_command(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-i",
                str(input_path),
                "-af",
                f"atempo={tempo:.6f},loudnorm=I=-16:TP=-1.5:LRA=11",
                "-ar",
                "48000",
                "-ac",
                "1",
                str(output_path),
            ],
            timeout_seconds=600,
            stage="narration_generation",
        )
        return output_path, self.audio_duration_seconds(output_path)

    def mix_longform_audio(
        self,
        *,
        narration_path: Path,
        duration_seconds: float,
        output_path: Path,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.mock_mode:
            shutil.copy2(narration_path, output_path)
            return output_path
        music_source = (
            f"anoisesrc=color=pink:amplitude=0.035:sample_rate=48000:d={duration_seconds:.2f}"
        )
        filter_graph = (
            "[1:a]highpass=f=90,lowpass=f=1800,volume=-25dB,"
            "afade=t=in:st=0:d=2,"
            f"afade=t=out:st={max(duration_seconds - 3, 0):.2f}:d=3[music];"
            "[music][0:a]sidechaincompress=threshold=0.015:ratio=8:attack=80:release=650[ducked];"
            "[0:a][ducked]amix=inputs=2:duration=first:weights='1 0.55',alimiter=limit=0.94[out]"
        )
        run_command(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-i",
                str(narration_path),
                "-f",
                "lavfi",
                "-i",
                music_source,
                "-filter_complex",
                filter_graph,
                "-map",
                "[out]",
                "-ar",
                "48000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ],
            timeout_seconds=600,
            stage="sound_design",
        )
        return output_path

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
            segment_filter = self._segment_filter(
                duration_seconds=segment.duration_seconds,
                variant=index,
            )
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
                    segment_filter,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "2",
                    "-preset",
                    "superfast",
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
                "-threads",
                "2",
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

        subtitle_filter = self._subtitle_filter(subtitle_path)
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
                "-threads",
                "2",
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

    def render_longform(
        self,
        *,
        plan: AssetPlan,
        audio_path: Path,
        working_dir: Path,
        output_path: Path,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.mock_mode:
            return self._render_mock_longform(audio_path=audio_path, output_path=output_path)

        segments_dir = working_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)
        segment_paths: list[Path] = []
        for index, segment in enumerate(plan.segments, start=1):
            segment_path = segments_dir / f"long-segment-{index:03d}.mp4"
            segment_filter = self._long_segment_filter(
                duration_seconds=segment.duration_seconds,
                variant=index,
            )
            run_command(
                [
                    self.settings.ffmpeg_binary,
                    "-y",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(segment.clip_path),
                    "-t",
                    f"{segment.duration_seconds:.2f}",
                    "-vf",
                    segment_filter,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "2",
                    "-preset",
                    "superfast",
                    "-crf",
                    "23",
                    str(segment_path),
                ],
                timeout_seconds=900,
                stage="video_rendering",
            )
            segment_paths.append(segment_path)

        concat_file = working_dir / "long-concat.txt"
        concat_lines = []
        for path in segment_paths:
            resolved_path = str(path.resolve()).replace("\\", "/")
            concat_lines.append(f"file '{resolved_path}'")
        write_text(concat_file, "\n".join(concat_lines) + "\n")
        rough_cut_path = working_dir / "long-rough-cut.mp4"
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
                "-threads",
                "2",
                "-preset",
                "medium",
                "-crf",
                "22",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(output_path),
            ],
            timeout_seconds=1800,
            stage="video_rendering",
        )
        return output_path

    def extract_frame(self, *, video_path: Path, output_path: Path, timestamp_seconds: float = 1.0) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.mock_mode or not video_path.exists() or self._is_placeholder_video(video_path):
            output_path.write_bytes(b"")
            return output_path
        run_command(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-ss",
                f"{timestamp_seconds:.2f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(output_path),
            ],
            timeout_seconds=120,
            stage="thumbnail_generation",
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

    def validate_long_video(self, video_path: Path, *, min_seconds: int, max_seconds: int) -> dict[str, object]:
        payload = self._probe_video(video_path, stage="validation")
        stream = payload["streams"][0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        duration = float(stream.get("duration") or payload.get("format", {}).get("duration") or 0)
        if width != 1280 or height != 720:
            raise PipelineStageError(
                stage="validation",
                message=f"Rendered long-form video has unexpected dimensions {width}x{height}.",
                probable_cause="The FFmpeg long-form render did not produce a 1280x720 frame.",
            )
        min_allowed = min_seconds - LONG_VIDEO_DURATION_TOLERANCE_SECONDS
        max_allowed = max_seconds + LONG_VIDEO_DURATION_TOLERANCE_SECONDS
        if not min_allowed <= duration <= max_allowed:
            raise PipelineStageError(
                stage="validation",
                message=(
                    f"Rendered long-form video duration is {duration:.2f}s, outside "
                    f"{min_seconds}-{max_seconds}s."
                ),
                probable_cause="Narration generation or audio duration fitting did not hit the required range.",
            )
        return payload

    def _probe_video(self, video_path: Path, *, stage: str) -> dict[str, object]:
        if not video_path.exists() or video_path.stat().st_size == 0:
            raise PipelineStageError(
                stage=stage,
                message="Rendered video was not created or is empty.",
                probable_cause="FFmpeg failed before producing the final MP4.",
            )
        ffprobe_binary = self._ffprobe_binary()
        if not command_exists(ffprobe_binary):
            return {"streams": [{"width": 0, "height": 0, "duration": 0}], "path": str(video_path)}
        result = run_command(
            [
                ffprobe_binary,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,duration",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(video_path),
            ],
            timeout_seconds=60,
            stage=stage,
        )
        payload = json.loads(result.stdout or "{}")
        if not payload.get("streams"):
            raise PipelineStageError(
                stage=stage,
                message="ffprobe could not read the rendered video stream.",
                probable_cause="The MP4 file may be corrupt.",
            )
        return payload

    def _ffprobe_binary(self) -> str:
        ffmpeg_path = Path(self.settings.ffmpeg_binary)
        if ffmpeg_path.exists():
            return str(ffmpeg_path.with_name("ffprobe.exe" if ffmpeg_path.suffix.lower() == ".exe" else "ffprobe"))
        return "ffprobe"

    def _escape_filter_path(self, path: Path) -> str:
        normalized = str(path.resolve()).replace("\\", "/")
        return normalized.replace(":", "\\:").replace("'", "\\'")

    def _subtitle_filter(self, subtitle_path: Path) -> str:
        if subtitle_path.suffix.lower() == ".ass":
            return f"subtitles=filename='{self._escape_filter_path(subtitle_path)}':original_size=1080x1920"
        style = (
            f"FontName={self.settings.subtitle_font_name},"
            f"FontSize={self.settings.subtitle_font_size},"
            f"Outline={self.settings.subtitle_outline},"
            f"MarginV={self.settings.subtitle_margin_v},"
            "Alignment=2,MarginL=96,MarginR=96,Shadow=0,Bold=1,"
            f"PrimaryColour={self.settings.subtitle_primary_color},"
            f"BackColour={self.settings.subtitle_back_color}"
        )
        return (
            f"subtitles=filename='{self._escape_filter_path(subtitle_path)}':original_size=1080x1920:"
            f"force_style='{style}'"
        )

    def _segment_filter(self, *, duration_seconds: float, variant: int) -> str:
        x_expr = (
            "(in_w-out_w)/2+42*sin(t*1.10)"
            if variant % 2
            else "(in_w-out_w)/2-38*sin(t*0.95)"
        )
        y_expr = (
            "(in_h-out_h)/2+28*cos(t*0.82)"
            if variant % 2
            else "(in_h-out_h)/2+34*sin(t*0.74)"
        )
        fade_out_start = max(duration_seconds - 0.18, 0.0)
        return (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920:x='{x_expr}':y='{y_expr}',"
            "eq=saturation=1.24:contrast=1.12:brightness=0.03:gamma=0.98,"
            "unsharp=5:5:0.75:3:3:0.0,"
            "fps=30,"
            f"fade=t=in:st=0:d=0.14,fade=t=out:st={fade_out_start:.2f}:d=0.14,"
            "format=yuv420p"
        )

    def _long_segment_filter(self, *, duration_seconds: float, variant: int) -> str:
        x_expr = (
            "(in_w-out_w)/2+30*sin(t*0.28)"
            if variant % 2
            else "(in_w-out_w)/2-26*sin(t*0.24)"
        )
        y_expr = (
            "(in_h-out_h)/2+18*cos(t*0.22)"
            if variant % 3
            else "(in_h-out_h)/2-16*sin(t*0.20)"
        )
        fade_out_start = max(duration_seconds - 0.35, 0.0)
        return (
            "scale=1280:720:force_original_aspect_ratio=increase,"
            f"crop=1280:720:x='{x_expr}':y='{y_expr}',"
            "eq=saturation=1.12:contrast=1.06:brightness=0.01,"
            "unsharp=5:5:0.45:3:3:0.0,"
            "fps=30,"
            f"fade=t=in:st=0:d=0.25,fade=t=out:st={fade_out_start:.2f}:d=0.25,"
            "format=yuv420p"
        )

    def _render_mock_short(
        self,
        *,
        audio_path: Path,
        subtitle_path: Path,
        output_path: Path,
    ) -> Path:
        if command_exists(self.settings.ffmpeg_binary):
            duration_seconds = self.audio_duration_seconds(audio_path)
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

    def _render_mock_longform(self, *, audio_path: Path, output_path: Path) -> Path:
        if command_exists(self.settings.ffmpeg_binary):
            duration_seconds = self.audio_duration_seconds(audio_path)
            run_command(
                [
                    self.settings.ffmpeg_binary,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=black:s=1280x720:r=30:d={duration_seconds:.2f}",
                    "-i",
                    str(audio_path),
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
                timeout_seconds=900,
                stage="video_rendering",
            )
            return output_path

        if self.settings.allow_placeholder_video:
            output_path.write_bytes(b"FAKE_MP4")
            return output_path

        raise PipelineStageError(
            stage="video_rendering",
            message="Cannot create a playable MP4 because FFmpeg is not installed.",
            probable_cause="Install FFmpeg and rerun.",
        )

    def _is_placeholder_video(self, video_path: Path) -> bool:
        try:
            return video_path.read_bytes() == b"FAKE_MP4"
        except OSError:
            return False
