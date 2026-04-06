from __future__ import annotations

from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import ConfigurationError, PipelineStageError
from youtube_kanaal.models.assets import SubtitleAsset
from youtube_kanaal.utils.files import write_text
from youtube_kanaal.utils.process import run_command
from youtube_kanaal.utils.subtitles import (
    align_script_to_reference_srt,
    build_ass_from_srt_text,
    build_timed_subtitles,
    build_vtt_from_srt_text,
    split_subtitle_lines,
)


class WhisperService:
    """whisper.cpp wrapper for generating subtitle timing from audio."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate_subtitles(
        self,
        *,
        audio_path: Path,
        subtitle_text: str,
        output_base_path: Path,
        duration_seconds: float,
    ) -> SubtitleAsset:
        output_base_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path = output_base_path.with_suffix(".srt")
        vtt_path = output_base_path.with_suffix(".vtt")
        ass_path = output_base_path.with_suffix(".ass")

        if self.settings.mock_mode:
            lines = split_subtitle_lines(subtitle_text)
            srt_text = build_timed_subtitles(lines, duration_seconds)
            write_text(srt_path, srt_text)
            write_text(vtt_path, build_vtt_from_srt_text(srt_text))
            return SubtitleAsset(srt_path=srt_path, vtt_path=vtt_path, ass_path=None)

        if not self.settings.whisper_model_path:
            raise ConfigurationError("WHISPER_MODEL_PATH is required for real subtitle generation.")

        run_command(
            [
                self.settings.whisper_cpp_binary,
                "-m",
                str(self.settings.whisper_model_path),
                "-f",
                str(audio_path),
                "-l",
                "en",
                "-ng",
                "-ml",
                "24",
                "-sow",
                "-osrt",
                "-of",
                str(output_base_path),
            ],
            timeout_seconds=300,
            stage="subtitle_generation",
        )

        if not srt_path.exists():
            raise PipelineStageError(
                stage="subtitle_generation",
                message="whisper.cpp did not create an SRT file.",
                probable_cause="Check whisper binary arguments and model path.",
            )
        normalized_srt_text = align_script_to_reference_srt(
            srt_path.read_text(encoding="utf-8"),
            subtitle_text,
        )
        write_text(srt_path, normalized_srt_text)
        write_text(vtt_path, build_vtt_from_srt_text(normalized_srt_text))
        write_text(
            ass_path,
            build_ass_from_srt_text(
                normalized_srt_text,
                font_name=self.settings.subtitle_font_name,
                font_size=self.settings.subtitle_font_size,
                margin_v=self.settings.subtitle_margin_v,
                outline=self.settings.subtitle_outline,
                primary_color=self.settings.subtitle_primary_color,
                highlight_color=self.settings.subtitle_highlight_color,
                outline_color=self.settings.subtitle_outline_color,
                back_color=self.settings.subtitle_back_color,
            ),
        )
        return SubtitleAsset(srt_path=srt_path, vtt_path=vtt_path if vtt_path.exists() else None, ass_path=ass_path)
