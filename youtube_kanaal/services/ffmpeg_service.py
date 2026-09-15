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
        target_seconds: int | None = None,
        preserve_natural_speed: bool = False,
    ) -> tuple[Path, float]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if target_seconds is None and min_seconds <= current_duration_seconds <= max_seconds:
            shutil.copy2(input_path, output_path)
            return output_path, current_duration_seconds

        if target_seconds is not None:
            target_duration = float(target_seconds)
        else:
            target_duration = min(max(current_duration_seconds, min_seconds + 8), max_seconds - 8)
            if current_duration_seconds < min_seconds:
                target_duration = min_seconds + 8
            elif current_duration_seconds > max_seconds:
                target_duration = max_seconds - 8
        if preserve_natural_speed:
            # Chatterbox tends to speak faster than the reference style. Keep a
            # relaxed pace for long-form videos and calibrate the script ranges
            # around this value; never speed up an overlong narration here.
            tempo = 0.88
            remaining_seconds = max(target_duration - (current_duration_seconds / tempo), 0.0)
            filter_graph = (
                f"atempo={tempo:.6f},loudnorm=I=-16:TP=-1.5:LRA=11,"
                f"apad=pad_dur={remaining_seconds:.3f},atrim=duration={target_duration:.3f}"
            )
        else:
            tempo = max(0.5, min(current_duration_seconds / target_duration, 2.0))
            filter_graph = f"atempo={tempo:.6f},loudnorm=I=-16:TP=-1.5:LRA=11"
        if self.settings.mock_mode and target_seconds is None:
            shutil.copy2(input_path, output_path)
            return output_path, current_duration_seconds
        if self.settings.mock_mode and not command_exists(self.settings.ffmpeg_binary):
            shutil.copy2(input_path, output_path)
            return output_path, current_duration_seconds
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
                energy=segment.energy,
                transition=segment.transition,
                visual_variant=segment.visual_variant,
                is_first=index == 1,
                is_last=index == len(plan.segments),
            )
            run_command(
                [
                    self.settings.ffmpeg_binary,
                    "-y",
                    "-ss",
                    f"{segment.start_offset_seconds:.2f}",
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
        subtitle_path: Path,
        working_dir: Path,
        output_path: Path,
        preview_mode: bool = False,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.mock_mode:
            return self._render_mock_longform(
                audio_path=audio_path,
                subtitle_path=subtitle_path,
                output_path=output_path,
            )

        segments_dir = working_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)
        width, height = (
            (self.settings.long_preview_width, self.settings.long_preview_height)
            if preview_mode
            else (self.settings.long_output_width, self.settings.long_output_height)
        )
        segment_paths: list[Path] = []
        crossfade_seconds = 0.40
        segment_render_durations: list[float] = []
        for index, segment in enumerate(plan.segments, start=1):
            segment_path = segments_dir / f"long-segment-{index:03d}.mp4"
            render_duration = round(segment.duration_seconds + (crossfade_seconds if index > 1 else 0.0), 2)
            segment_render_durations.append(render_duration)
            segment_filter = self._long_segment_filter(
                duration_seconds=render_duration,
                variant=index,
            )
            if segment.clip_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                title_path = working_dir / f"long-title-{index:03d}.txt"
                caption_path = working_dir / f"long-caption-{index:03d}.txt"
                write_text(title_path, self._clean_drawtext_text(segment.reason.split(":", 1)[0]))
                write_text(caption_path, self._clean_drawtext_text(segment.on_screen_text or segment.reason))
                photo_filter = (
                    self._long_overview_filter(
                        duration_seconds=render_duration,
                        title_path=title_path,
                        caption_path=caption_path,
                        width=width,
                        height=height,
                        focus_x=segment.focus_x,
                        focus_y=segment.focus_y,
                        zoom_transition=segment.visual_type == "transition",
                    )
                    if segment.clip_path.name == "long-overview.jpg"
                    else self._long_photo_filter(
                        duration_seconds=render_duration,
                        variant=index,
                        title_path=title_path,
                        caption_path=caption_path,
                        width=width,
                        height=height,
                        reveal=segment.visual_type == "transition",
                        motion=segment.motion,
                    )
                )
                run_command(
                    [
                        self.settings.ffmpeg_binary,
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c=white:s={width}x{height}:r=30:d={render_duration:.2f}",
                        "-loop",
                        "1",
                        "-framerate",
                        "30",
                        "-i",
                        str(segment.clip_path),
                        "-filter_complex",
                        photo_filter,
                        "-map",
                        "[v]",
                        "-t",
                        f"{render_duration:.2f}",
                        "-r",
                        "30",
                        "-fps_mode",
                        "cfr",
                        "-an",
                        "-c:v",
                        "libx264",
                        "-threads",
                        "2",
                        "-preset",
                        "superfast",
                        "-crf",
                        "23",
                        "-pix_fmt",
                        "yuv420p",
                        str(segment_path),
                    ],
                    timeout_seconds=900,
                    stage="video_rendering",
                )
            else:
                run_command(
                    [
                        self.settings.ffmpeg_binary,
                        "-y",
                        "-stream_loop",
                        "-1",
                        "-i",
                        str(segment.clip_path),
                        "-t",
                        f"{render_duration:.2f}",
                        "-vf",
                        segment_filter,
                        "-r",
                        "30",
                        "-fps_mode",
                        "cfr",
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
        filter_parts = [f"[{index}:v]fps=30,settb=AVTB[v{index}]" for index in range(len(segment_paths))]
        current_label = "v0"
        current_duration = segment_render_durations[0]
        for index in range(1, len(segment_paths)):
            offset = max(current_duration - crossfade_seconds, 0.0)
            next_label = f"vx{index}"
            filter_parts.append(
                f"[{current_label}][v{index}]xfade=transition=fade:duration={crossfade_seconds:.2f}:"
                f"offset={offset:.2f},fps=30,settb=AVTB[{next_label}]"
            )
            current_label = next_label
            current_duration = offset + segment_render_durations[index]
        filter_parts.append(f"[{current_label}]format=yuv420p[vout]")
        video_inputs: list[str] = []
        for path in segment_paths:
            video_inputs.extend(["-i", str(path)])
        run_command(
            [
                self.settings.ffmpeg_binary,
                "-y",
                *video_inputs,
                "-i",
                str(audio_path),
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                "[vout]",
                "-map",
                f"{len(segment_paths)}:a:0",
                "-t",
                f"{plan.total_duration_seconds:.2f}",
                "-c:v",
                "libx264",
                "-threads",
                "2",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "30",
                "-fps_mode",
                "cfr",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(rough_cut_path),
            ],
            timeout_seconds=1800,
            stage="video_rendering",
        )
        subtitle_filter = self._subtitle_filter(subtitle_path, original_size=(width, height))
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
                "18",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "30",
                "-fps_mode",
                "cfr",
                "-c:a",
                "copy",
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

    def validate_long_video(
        self,
        video_path: Path,
        *,
        min_seconds: int,
        max_seconds: int,
        expected_width: int = 1280,
        expected_height: int = 720,
    ) -> dict[str, object]:
        payload = self._probe_video(video_path, stage="validation")
        stream = payload["streams"][0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        duration = float(stream.get("duration") or payload.get("format", {}).get("duration") or 0)
        if width != expected_width or height != expected_height:
            raise PipelineStageError(
                stage="validation",
                message=f"Rendered long-form video has unexpected dimensions {width}x{height}.",
                probable_cause=f"The FFmpeg long-form render did not produce a {expected_width}x{expected_height} frame.",
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

    def _subtitle_filter(self, subtitle_path: Path, *, original_size: tuple[int, int] = (1080, 1920)) -> str:
        if subtitle_path.suffix.lower() == ".ass":
            return f"subtitles=filename='{self._escape_filter_path(subtitle_path)}'"
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
            f"subtitles=filename='{self._escape_filter_path(subtitle_path)}':original_size={original_size[0]}x{original_size[1]}:"
            f"force_style='{style}'"
        )

    def _long_photo_filter(
        self,
        *,
        duration_seconds: float,
        variant: int,
        title_path: Path,
        caption_path: Path,
        width: int,
        height: int,
        reveal: bool = False,
        motion: bool = True,
    ) -> str:
        card_width = round(width * 0.40625)
        card_height = round(height * 0.5)
        card_x = (width - card_width) // 2
        card_y = round(height * 0.19)
        title_size = round(width * 0.0234)
        motion_scale = 1.18 if reveal else 1.06
        if not motion:
            motion_scale = 1.0
        source_width = round(card_width * motion_scale)
        source_height = round(card_height * motion_scale)
        pan_x = round(source_width * 0.04)
        pan_y = round(source_height * 0.03)
        title_file = self._escape_filter_path(title_path)
        if reveal:
            crop_x = f"(iw-{card_width})*0.62-(iw-{card_width})*0.12*(1-cos(t*1.8))"
            crop_y = f"(ih-{card_height})*0.58-(ih-{card_height})*0.08*(1-cos(t*1.5))"
        else:
            crop_x = (
                f"(iw-{card_width})/2+{pan_x}*sin(t*0.35)"
                if motion
                else f"(iw-{card_width})/2"
            )
            crop_y = (
                f"(ih-{card_height})/2+{pan_y}*cos(t*0.28)"
                if motion
                else f"(ih-{card_height})/2"
            )
        return (
            f"[1:v]scale={source_width}:{source_height}:force_original_aspect_ratio=increase,"
            f"crop={card_width}:{card_height}:x='{crop_x}':y='{crop_y}',"
            "eq=saturation=1.1:contrast=1.05:brightness=0.01,"
            "unsharp=5:5:0.45:3:3:0.0[photo];"
            f"[0:v][photo]overlay=x={card_x}:y={card_y}:shortest=1[card];"
            f"[card]drawtext=font='Arial':textfile='{title_file}':fontcolor=black:"
            f"fontsize={title_size}:x=(w-text_w)/2:y={round(height * 0.03)}:expansion=none:enable='between(t,0,{duration_seconds:.2f})',"
            "fps=30,settb=AVTB,format=yuv420p[v]"
        )

    def _long_overview_filter(
        self,
        *,
        duration_seconds: float,
        title_path: Path,
        caption_path: Path,
        width: int,
        height: int,
        focus_x: float = 0.5,
        focus_y: float = 0.5,
        zoom_transition: bool = False,
    ) -> str:
        if zoom_transition:
            zoom_width = round(width * 1.18)
            zoom_height = round(height * 1.18)
            crop_x = round((zoom_width - width) * focus_x)
            crop_y = round((zoom_height - height) * focus_y)
            return (
                f"[1:v]scale={zoom_width}:{zoom_height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}:x={crop_x}:y={crop_y},"
                "fps=30,settb=AVTB,format=yuv420p[v]"
            )
        return (
            f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=white[overview];"
            f"[0:v][overview]overlay=x=0:y=0[card];"
                "[card]fps=30,settb=AVTB,format=yuv420p[v]"
        )

    @staticmethod
    def _clean_drawtext_text(value: str) -> str:
        return " ".join(value.replace("\n", " ").split())[:120]

    def _segment_filter(
        self,
        *,
        duration_seconds: float,
        variant: int,
        energy: str = "medium",
        transition: str = "cut",
        visual_variant: str = "primary",
        is_first: bool = False,
        is_last: bool = False,
    ) -> str:
        energy_key = energy if energy in {"low", "medium", "high"} else "medium"
        scale_sizes = {
            "low": (1100, 1956),
            "medium": (1140, 2027),
            "high": (1188, 2112),
        }
        frequency = {"low": 0.48, "medium": 0.82, "high": 1.35}[energy_key]
        amplitude_x = {"low": 10, "medium": 24, "high": 42}[energy_key]
        amplitude_y = {"low": 8, "medium": 18, "high": 30}[energy_key]
        scale_width, scale_height = scale_sizes[energy_key]
        variant_scale = {
            "primary": 1.0,
            "cutaway": 1.025,
            "proof": 1.045,
            "punch": 1.09,
            "reveal": 1.075,
        }.get(visual_variant, 1.0)
        if transition == "punch":
            variant_scale += 0.035
        scale_width = int(scale_width * variant_scale)
        scale_height = int(scale_height * variant_scale)
        x_expr = (
            f"(in_w-out_w)/2+{amplitude_x}*sin(t*{frequency:.2f})"
            if variant % 2
            else f"(in_w-out_w)/2-{amplitude_x}*sin(t*{frequency * 0.91:.2f})"
        )
        y_expr = (
            f"(in_h-out_h)/2+{amplitude_y}*cos(t*{frequency * 0.77:.2f})"
            if variant % 2
            else f"(in_h-out_h)/2+{amplitude_y}*sin(t*{frequency * 0.69:.2f})"
        )
        fade_out_start = max(duration_seconds - 0.18, 0.0)
        fades: list[str] = []
        if is_first:
            fades.append("fade=t=in:st=0:d=0.08")
        if is_last:
            fades.append(f"fade=t=out:st={fade_out_start:.2f}:d=0.12")
        if visual_variant == "reveal":
            fades.append("fade=t=in:st=0:d=0.05")
        fade_filter = ",".join(fades)
        if fade_filter:
            fade_filter += ","
        grade = {
            "primary": "eq=saturation=1.12:contrast=1.07:brightness=0.015:gamma=0.99",
            "cutaway": "eq=saturation=1.16:contrast=1.09:brightness=0.02:gamma=0.99",
            "proof": "eq=saturation=1.18:contrast=1.11:brightness=0.02:gamma=0.98",
            "punch": "eq=saturation=1.24:contrast=1.13:brightness=0.025:gamma=0.98",
            "reveal": "eq=saturation=1.28:contrast=1.15:brightness=0.035:gamma=0.97",
        }.get(visual_variant, "eq=saturation=1.12:contrast=1.07:brightness=0.015:gamma=0.99")
        return (
            f"scale={scale_width}:{scale_height}:force_original_aspect_ratio=increase,"
            f"crop=1080:1920:x='{x_expr}':y='{y_expr}',"
            f"{grade},"
            "unsharp=5:5:0.55:3:3:0.0,"
            "fps=30,"
            f"{fade_filter}"
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

    def _render_mock_longform(self, *, audio_path: Path, subtitle_path: Path, output_path: Path) -> Path:
        if command_exists(self.settings.ffmpeg_binary):
            duration_seconds = self.audio_duration_seconds(audio_path)
            rough_path = output_path.with_name(f"{output_path.stem}-rough.mp4")
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
                    str(rough_path),
                ],
                timeout_seconds=900,
                stage="video_rendering",
            )
            run_command(
                [
                    self.settings.ffmpeg_binary,
                    "-y",
                    "-i",
                    str(rough_path),
                    "-vf",
                    self._subtitle_filter(subtitle_path, original_size=(1280, 720)),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-c:a",
                    "copy",
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
