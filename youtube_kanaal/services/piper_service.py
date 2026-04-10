from __future__ import annotations

import logging
import math
import struct
import wave
from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import ConfigurationError, PipelineStageError
from youtube_kanaal.utils.process import command_exists, run_command
from youtube_kanaal.utils.subtitles import estimate_runtime_from_text


class PiperService:
    """Wrapper for Piper TTS."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def runtime_ready(self) -> tuple[bool, str | None]:
        if self.settings.mock_mode:
            return True, None
        if not command_exists(self.settings.piper_binary):
            return False, f"Piper binary not found: {self.settings.piper_binary}"
        try:
            voice_model_path = self._resolve_voice_model_path()
        except ConfigurationError as exc:
            return False, str(exc)
        if not voice_model_path.exists():
            return False, f"Piper voice model not found: {voice_model_path}"
        return True, None

    def synthesize(
        self,
        *,
        text: str,
        output_path: Path,
        logger: logging.Logger | None = None,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.mock_mode:
            self._write_mock_wave(output_path, estimate_runtime_from_text(text))
            self._validate_output(output_path)
            return output_path

        voice_model_path = self._resolve_voice_model_path()
        if logger:
            logger.info(
                "piper_synthesis: start",
                extra={
                    "stage": "narration_generation",
                    "engine": "piper",
                    "output_path": str(output_path),
                    "voice_model_path": str(voice_model_path),
                },
            )
        try:
            run_command(
                [
                    self.settings.piper_binary,
                    "--model",
                    str(voice_model_path),
                    "--length-scale",
                    str(self.settings.piper_length_scale),
                    "--noise-scale",
                    str(self.settings.piper_noise_scale),
                    "--noise-w-scale",
                    str(self.settings.piper_noise_w_scale),
                    "--sentence-silence",
                    str(self.settings.piper_sentence_silence),
                    "--output_file",
                    str(output_path),
                ],
                input_text=text,
                timeout_seconds=300,
                stage="narration_generation",
            )
        except Exception as exc:
            if isinstance(exc, PipelineStageError):
                raise
            raise PipelineStageError(
                stage="narration_generation",
                message="Piper synthesis failed.",
                probable_cause=str(exc),
            ) from exc
        self._validate_output(output_path)
        if logger:
            logger.info(
                "piper_synthesis: finish",
                extra={
                    "stage": "narration_generation",
                    "engine": "piper",
                    "output_path": str(output_path),
                    "size_bytes": output_path.stat().st_size,
                },
            )
        return output_path

    def _resolve_voice_model_path(self) -> Path:
        if self.settings.piper_voice_model_path:
            return self.settings.piper_voice_model_path
        inferred = self.settings.cache_dir / "piper" / f"{self.settings.default_piper_voice}.onnx"
        if inferred.exists():
            return inferred
        raise ConfigurationError(
            "PIPER_VOICE_MODEL_PATH is required unless the inferred cache voice file exists."
        )

    def _validate_output(self, output_path: Path) -> None:
        if not output_path.exists():
            raise PipelineStageError(
                stage="narration_generation",
                message="Piper did not create the expected narration WAV.",
                probable_cause=f"Expected output file is missing: {output_path}",
            )
        if output_path.stat().st_size == 0:
            raise PipelineStageError(
                stage="narration_generation",
                message="Piper created an empty narration WAV.",
                probable_cause=f"Output file is empty: {output_path}",
            )

    def _write_mock_wave(self, output_path: Path, duration_seconds: float) -> None:
        sample_rate = 16_000
        amplitude = 8_000
        frequency = 220.0
        total_frames = int(sample_rate * duration_seconds)
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            for index in range(total_frames):
                value = int(amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
                wav_file.writeframes(struct.pack("<h", value))
