from __future__ import annotations

import logging
import math
import struct
import wave
from pathlib import Path
from typing import Any

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.utils.process import command_exists
from youtube_kanaal.utils.subtitles import estimate_runtime_from_text

KOKORO_SAMPLE_RATE = 24_000


class KokoroService:
    """Local Kokoro TTS wrapper for short English voice-overs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._pipeline: Any | None = None

    def runtime_ready(self) -> tuple[bool, str | None]:
        if self.settings.mock_mode:
            return True, None
        try:
            self._import_pipeline()
        except ImportError:
            return False, "Kokoro Python package not installed. Run: pip install 'kokoro>=0.9.4'"
        if not command_exists(self.settings.kokoro_espeak_binary) and not self._espeak_loader_available():
            return False, f"espeak-ng not found: {self.settings.kokoro_espeak_binary}"
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

        if logger:
            logger.info(
                "kokoro_synthesis: start",
                extra={
                    "stage": "narration_generation",
                    "engine": "kokoro",
                    "voice": self.settings.kokoro_voice,
                    "lang_code": self.settings.kokoro_lang_code,
                    "speed": self.settings.kokoro_speed,
                    "device": self.settings.kokoro_device,
                    "output_path": str(output_path),
                },
            )
        try:
            audio = self._generate_audio(text)
            self._write_audio_wave(output_path, audio)
        except Exception as exc:
            if isinstance(exc, PipelineStageError):
                raise
            raise PipelineStageError(
                stage="narration_generation",
                message="Kokoro synthesis failed.",
                probable_cause=str(exc),
            ) from exc

        self._validate_output(output_path)
        if logger:
            logger.info(
                "kokoro_synthesis: finish",
                extra={
                    "stage": "narration_generation",
                    "engine": "kokoro",
                    "output_path": str(output_path),
                    "size_bytes": output_path.stat().st_size,
                },
            )
        return output_path

    def _generate_audio(self, text: str) -> object:
        pipeline = self._get_pipeline()
        chunks = []
        generator = pipeline(
            text,
            voice=self.settings.kokoro_voice,
            speed=self.settings.kokoro_speed,
        )
        for _, _, audio in generator:
            chunks.append(audio)
        if not chunks:
            raise PipelineStageError(
                stage="narration_generation",
                message="Kokoro returned no audio chunks.",
                probable_cause="The text may be empty or Kokoro failed before yielding audio.",
            )
        return chunks

    def _get_pipeline(self) -> object:
        if self._pipeline is not None:
            return self._pipeline
        pipeline_cls = self._import_pipeline()
        kwargs: dict[str, object] = {"lang_code": self.settings.kokoro_lang_code}
        if self.settings.kokoro_device != "auto":
            kwargs["device"] = self.settings.kokoro_device
        self._pipeline = pipeline_cls(**kwargs)
        return self._pipeline

    def _import_pipeline(self) -> type:
        from kokoro import KPipeline  # type: ignore

        return KPipeline

    def _espeak_loader_available(self) -> bool:
        try:
            import espeakng_loader  # noqa: F401
        except ImportError:
            return False
        return True

    def _write_audio_wave(self, output_path: Path, audio_chunks: object) -> None:
        import numpy as np

        arrays = []
        for chunk in audio_chunks if isinstance(audio_chunks, list) else [audio_chunks]:
            if hasattr(chunk, "detach"):
                chunk = chunk.detach().cpu().numpy()
            arrays.append(np.asarray(chunk, dtype=np.float32).reshape(-1))
        audio = np.concatenate(arrays) if len(arrays) > 1 else arrays[0]
        audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
        audio = np.clip(audio, -1.0, 1.0)
        pcm = (audio * 32767.0).astype("<i2")
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(KOKORO_SAMPLE_RATE)
            wav_file.writeframes(pcm.tobytes())

    def _validate_output(self, output_path: Path) -> None:
        if not output_path.exists():
            raise PipelineStageError(
                stage="narration_generation",
                message="Kokoro did not create the expected narration WAV.",
                probable_cause=f"Expected output file is missing: {output_path}",
            )
        if output_path.stat().st_size == 0:
            raise PipelineStageError(
                stage="narration_generation",
                message="Kokoro created an empty narration WAV.",
                probable_cause=f"Output file is empty: {output_path}",
            )

    def _write_mock_wave(self, output_path: Path, duration_seconds: float) -> None:
        amplitude = 8_000
        frequency = 240.0
        total_frames = int(KOKORO_SAMPLE_RATE * duration_seconds)
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(KOKORO_SAMPLE_RATE)
            for index in range(total_frames):
                value = int(amplitude * math.sin(2 * math.pi * frequency * index / KOKORO_SAMPLE_RATE))
                wav_file.writeframes(struct.pack("<h", value))
