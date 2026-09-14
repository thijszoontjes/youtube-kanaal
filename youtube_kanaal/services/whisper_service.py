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
    normalize_whisper_srt,
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
        beat_overlays: list[dict[str, object]] | None = None,
        style_profile: str = "short",
    ) -> SubtitleAsset:
        output_base_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path = output_base_path.with_suffix(".srt")
        vtt_path = output_base_path.with_suffix(".vtt")
        ass_path = output_base_path.with_suffix(".ass")

        is_long = style_profile == "long"
        style = {
            "font_size": 36 if is_long else max(self.settings.subtitle_font_size, 72),
            "margin_v": 28 if is_long else self.settings.subtitle_margin_v,
            "outline": 2 if is_long else self.settings.subtitle_outline,
            "margin_l": 80 if is_long else 96,
            "margin_r": 80 if is_long else 96,
            "play_res_x": 1280 if is_long else 1080,
            "play_res_y": 720 if is_long else 1920,
            "style_name": "LongForm" if is_long else "Shorts",
            "primary_color": "&H00000000" if is_long else self.settings.subtitle_primary_color,
            "highlight_color": "&H00000000" if is_long else self.settings.subtitle_highlight_color,
            "outline_color": "&H00FFFFFF" if is_long else self.settings.subtitle_outline_color,
            "back_color": "&H64FFFFFF" if is_long else self.settings.subtitle_back_color,
        }

        if self.settings.mock_mode:
            lines = split_subtitle_lines(subtitle_text)
            srt_text = build_timed_subtitles(lines, duration_seconds)
            write_text(srt_path, srt_text)
            write_text(vtt_path, build_vtt_from_srt_text(srt_text))
            write_text(
                ass_path,
                build_ass_from_srt_text(
                    srt_text,
                    font_name=self.settings.subtitle_font_name,
                    font_size=style["font_size"],
                    margin_v=style["margin_v"],
                    outline=style["outline"],
                    primary_color=style["primary_color"],
                    highlight_color=style["highlight_color"],
                    outline_color=style["outline_color"],
                    back_color=style["back_color"],
                    margin_l=style["margin_l"],
                    margin_r=style["margin_r"],
                    style_name=style["style_name"],
                    play_res_x=style["play_res_x"],
                    play_res_y=style["play_res_y"],
                ),
            )
            return SubtitleAsset(srt_path=srt_path, vtt_path=vtt_path, ass_path=ass_path)

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
        raw_srt_text = srt_path.read_text(encoding="utf-8")
        normalized_srt_text = (
            normalize_whisper_srt(raw_srt_text)
            if is_long
            else align_script_to_reference_srt(raw_srt_text, subtitle_text)
        )
        write_text(srt_path, normalized_srt_text)
        write_text(vtt_path, build_vtt_from_srt_text(normalized_srt_text))
        write_text(
            ass_path,
            build_ass_from_srt_text(
                normalized_srt_text,
                font_name=self.settings.subtitle_font_name,
                font_size=style["font_size"],
                margin_v=style["margin_v"],
                outline=style["outline"],
                primary_color=style["primary_color"],
                highlight_color=style["highlight_color"],
                outline_color=style["outline_color"],
                back_color=style["back_color"],
                margin_l=style["margin_l"],
                margin_r=style["margin_r"],
                style_name=style["style_name"],
                play_res_x=style["play_res_x"],
                play_res_y=style["play_res_y"],
                beat_overlays=beat_overlays,
            ),
        )
        return SubtitleAsset(srt_path=srt_path, vtt_path=vtt_path if vtt_path.exists() else None, ass_path=ass_path)
