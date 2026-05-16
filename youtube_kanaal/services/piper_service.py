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

MINIMUM_PIPER_MODEL_SIZE_BYTES = 1_048_576


class PiperService:
    """Wrapper for Piper TTS."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model_probe_cache: dict[Path, tuple[int, int, bool, str | None]] = {}

    def runtime_ready(self) -> tuple[bool, str | None]:
        if self.settings.mock_mode:
            return True, None
        if not command_exists(self.settings.piper_binary):
            return False, f"Piper binary not found: {self.settings.piper_binary}"
        try:
            self._resolve_voice_model_path()
        except ConfigurationError as exc:
            return False, str(exc)
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

        requested_voice_model_path = self._requested_voice_model_path()
        voice_model_path = self._resolve_voice_model_path()
        if logger:
            if not self._same_path(requested_voice_model_path, voice_model_path):
                logger.warning(
                    "piper_voice_model_fallback",
                    extra={
                        "stage": "narration_generation",
                        "engine": "piper",
                        "requested_voice_model_path": str(requested_voice_model_path),
                        "resolved_voice_model_path": str(voice_model_path),
                    },
                )
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

    def describe_voice_model(self) -> tuple[bool, Path | None, str | None]:
        requested_voice_model_path = self._requested_voice_model_path()
        try:
            resolved_voice_model_path = self._resolve_voice_model_path()
        except ConfigurationError as exc:
            return False, None, str(exc)

        if self._same_path(requested_voice_model_path, resolved_voice_model_path):
            return True, resolved_voice_model_path, None

        _, requested_reason = self._probe_voice_model(requested_voice_model_path)
        fallback_reason = requested_reason or "configured model is not usable"
        return (
            True,
            resolved_voice_model_path,
            (
                f"Configured Piper voice model is unusable: {requested_voice_model_path} "
                f"({fallback_reason}). Falling back to {resolved_voice_model_path}."
            ),
        )

    def _resolve_voice_model_path(self) -> Path:
        candidates = [self._requested_voice_model_path(), *self._fallback_voice_model_candidates()]
        seen: set[Path] = set()
        failures: list[str] = []

        for candidate in candidates:
            normalized = candidate.expanduser().resolve(strict=False)
            if normalized in seen:
                continue
            seen.add(normalized)
            usable, reason = self._probe_voice_model(candidate)
            if usable:
                return candidate
            failures.append(f"{candidate}: {reason}")

        raise ConfigurationError(
            "No usable Piper voice model found. "
            "Re-download the configured model or point PIPER_VOICE_MODEL_PATH at a valid .onnx file. "
            + " | ".join(failures)
        )

    def _requested_voice_model_path(self) -> Path:
        if self.settings.piper_voice_model_path:
            return self.settings.piper_voice_model_path
        return self.settings.cache_dir / "piper" / f"{self.settings.default_piper_voice}.onnx"

    def _fallback_voice_model_candidates(self) -> list[Path]:
        cache_dir = self.settings.cache_dir / "piper"
        if not cache_dir.exists():
            return []
        requested_voice_model_path = self._requested_voice_model_path()
        return [
            path
            for path in sorted(cache_dir.glob("*.onnx"))
            if not self._same_path(path, requested_voice_model_path)
        ]

    def _probe_voice_model(self, path: Path) -> tuple[bool, str | None]:
        if not path.exists():
            return False, "file does not exist"

        stat = path.stat()
        normalized = path.expanduser().resolve(strict=False)
        cached = self._model_probe_cache.get(normalized)
        if cached and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
            return cached[2], cached[3]

        if stat.st_size < MINIMUM_PIPER_MODEL_SIZE_BYTES:
            result = (False, f"file is unexpectedly small ({stat.st_size} bytes)")
        else:
            onnx_reason = self._probe_onnx_model(path)
            result = (onnx_reason is None, onnx_reason)

        self._model_probe_cache[normalized] = (
            stat.st_size,
            stat.st_mtime_ns,
            result[0],
            result[1],
        )
        return result

    def _probe_onnx_model(self, path: Path) -> str | None:
        try:
            import onnxruntime  # type: ignore
        except ImportError:
            return None

        try:
            onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        except Exception as exc:
            return f"ONNX validation failed: {exc}"
        return None

    def _same_path(self, left: Path, right: Path) -> bool:
        return left.expanduser().resolve(strict=False) == right.expanduser().resolve(strict=False)

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
