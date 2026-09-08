from __future__ import annotations

import importlib
import logging
import math
import struct
import wave
from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import ConfigurationError, PipelineStageError
from youtube_kanaal.utils.process import command_exists, run_command
from youtube_kanaal.utils.subtitles import estimate_runtime_from_text


SUPPORTED_REFERENCE_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}


class ChatterboxService:
    """Local Chatterbox voice cloning for English narration."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None

    def runtime_ready(self) -> tuple[bool, str | None]:
        if self.settings.mock_mode:
            return True, None
        try:
            importlib.import_module("chatterbox.tts_turbo")
            importlib.import_module("torch")
            importlib.import_module("torchaudio")
        except Exception as exc:
            return False, f"Chatterbox is not installed or failed to import: {exc}"
        return True, None

    def discover_reference_sources(self) -> list[Path]:
        candidates: list[Path] = []
        if self.settings.xtts_speaker_wav_path:
            candidates.append(self.settings.xtts_speaker_wav_path)
        if self.settings.xtts_speaker_wav_dir and self.settings.xtts_speaker_wav_dir.exists():
            candidates.extend(
                path
                for path in sorted(self.settings.xtts_speaker_wav_dir.iterdir())
                if path.is_file() and path.suffix.lower() in SUPPORTED_REFERENCE_EXTENSIONS
            )

        selected: list[Path] = []
        seen: set[Path] = set()
        for path in candidates:
            resolved = path.expanduser().resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if resolved.exists():
                selected.append(resolved)
        return selected

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
            return output_path

        references = self.discover_reference_sources()
        if not references:
            raise ConfigurationError(
                "Chatterbox needs a reference recording. Add an English voice memo to "
                f"{self.settings.xtts_speaker_wav_dir}."
            )

        reference_path = self._prepare_reference_audio(references[0])
        try:
            model = self._load_model()
            waveform = model.generate(text, audio_prompt_path=str(reference_path))
            import torchaudio

            torchaudio.save(str(output_path), waveform.detach().cpu(), model.sr)
        except Exception as exc:
            if isinstance(exc, (ConfigurationError, PipelineStageError)):
                raise
            raise PipelineStageError(
                stage="narration_generation",
                message="Chatterbox synthesis failed.",
                probable_cause=str(exc),
            ) from exc

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise PipelineStageError(
                stage="narration_generation",
                message="Chatterbox did not create the expected narration WAV.",
                probable_cause=str(output_path),
            )
        if logger:
            logger.info(
                "chatterbox_synthesis: finish",
                extra={
                    "stage": "narration_generation",
                    "engine": "chatterbox",
                    "model": self.settings.chatterbox_model,
                    "device": self._resolve_device(),
                    "reference_path": str(reference_path),
                    "output_path": str(output_path),
                },
            )
        return output_path

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            if self.settings.chatterbox_model == "standard":
                from chatterbox.tts import ChatterboxTTS

                self._model = ChatterboxTTS.from_pretrained(device=self._resolve_device())
            else:
                from chatterbox.tts_turbo import ChatterboxTurboTTS

                self._model = ChatterboxTurboTTS.from_pretrained(device=self._resolve_device())
        except Exception as exc:
            raise PipelineStageError(
                stage="narration_generation",
                message="Chatterbox model could not be loaded.",
                probable_cause=str(exc),
            ) from exc
        return self._model

    def _resolve_device(self) -> str:
        requested = self.settings.chatterbox_device
        if requested != "auto":
            return requested
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _prepare_reference_audio(self, source_path: Path) -> Path:
        prepared_path = self.settings.cache_dir / "chatterbox" / "reference.wav"
        prepared_path.parent.mkdir(parents=True, exist_ok=True)
        if not command_exists(self.settings.ffmpeg_binary):
            raise ConfigurationError("Chatterbox requires FFmpeg to prepare the reference audio.")
        run_command(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-i",
                str(source_path),
                "-t",
                "30",
                "-vn",
                "-ar",
                "24000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(prepared_path),
            ],
            timeout_seconds=300,
            stage="narration_generation",
        )
        return prepared_path

    def _write_mock_wave(self, output_path: Path, duration_seconds: float) -> None:
        sample_rate = 22050
        frame_count = max(int(sample_rate * duration_seconds), 1)
        amplitude = 900
        frequency = 220
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            frames = bytearray()
            for index in range(frame_count):
                sample = int(amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
                frames.extend(struct.pack("<h", sample))
            wav_file.writeframes(frames)
