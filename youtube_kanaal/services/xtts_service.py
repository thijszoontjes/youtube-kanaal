from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from youtube_kanaal.config import Settings, project_root
from youtube_kanaal.exceptions import ConfigurationError, PipelineStageError
from youtube_kanaal.utils.process import run_command
from youtube_kanaal.utils.subtitles import estimate_runtime_from_text

SUPPORTED_REFERENCE_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}


class XTTSService:
    """Free multilingual voice cloning backed by Coqui XTTS."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def synthesize(self, *, text: str, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.mock_mode:
            self._write_mock_wave(output_path, estimate_runtime_from_text(text))
            return output_path

        reference_paths = self.prepare_reference_audio()
        command = self._build_command(
            text=text,
            output_path=output_path,
            reference_paths=reference_paths,
        )
        try:
            run_command(
                command,
                timeout_seconds=self.settings.xtts_timeout_seconds,
                stage="narration_generation",
            )
        except Exception as exc:
            if isinstance(exc, PipelineStageError):
                raise
            raise PipelineStageError(
                stage="narration_generation",
                message="XTTS synthesis failed.",
                probable_cause=str(exc),
            ) from exc
        return output_path

    def discover_reference_sources(self) -> list[Path]:
        candidates: list[Path] = []
        if self.settings.xtts_speaker_wav_path:
            candidates.append(self.settings.xtts_speaker_wav_path)

        if self.settings.xtts_speaker_wav_dir and self.settings.xtts_speaker_wav_dir.exists():
            for path in sorted(self.settings.xtts_speaker_wav_dir.iterdir()):
                if path.is_file() and path.suffix.lower() in SUPPORTED_REFERENCE_EXTENSIONS:
                    candidates.append(path)

        existing_candidates: list[Path] = []
        seen: set[Path] = set()
        for path in candidates:
            resolved = path.expanduser().resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if resolved.exists():
                existing_candidates.append(resolved)

        return existing_candidates[: self.settings.xtts_max_reference_clips]

    def prepare_reference_audio(self) -> list[Path]:
        source_paths = self.discover_reference_sources()
        if not source_paths:
            configured_dir = self.settings.xtts_speaker_wav_dir
            source_hint = (
                f"Put 1-{self.settings.xtts_max_reference_clips} English voice memos in {configured_dir}"
                if configured_dir
                else "Set XTTS_SPEAKER_WAV_PATH to an English voice sample"
            )
            raise ConfigurationError(
                f"XTTS needs reference audio before it can clone your voice. {source_hint}."
            )

        prepared_dir = self.settings.cache_dir / "xtts" / "reference_audio"
        prepared_dir.mkdir(parents=True, exist_ok=True)

        prepared_paths: list[Path] = []
        for index, source_path in enumerate(source_paths, start=1):
            prepared_path = prepared_dir / f"reference-{index:02d}.wav"
            run_command(
                [
                    self.settings.ffmpeg_binary,
                    "-y",
                    "-i",
                    str(source_path),
                    "-t",
                    str(self.settings.xtts_reference_max_seconds),
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
            prepared_paths.append(prepared_path)
        return prepared_paths

    def _build_command(
        self,
        *,
        text: str,
        output_path: Path,
        reference_paths: list[Path],
    ) -> list[str]:
        if self.settings.xtts_runtime == "docker":
            return self._docker_command(
                text=text,
                output_path=output_path,
                reference_paths=reference_paths,
            )
        return self._binary_command(
            text=text,
            output_path=output_path,
            reference_paths=reference_paths,
        )

    def _binary_command(
        self,
        *,
        text: str,
        output_path: Path,
        reference_paths: list[Path],
    ) -> list[str]:
        command = [
            self.settings.xtts_binary,
            "--text",
            text,
            "--model_name",
            self.settings.xtts_model_name,
            "--language_idx",
            self.settings.xtts_language,
            "--speaker_wav",
            *[str(path) for path in reference_paths],
            "--out_path",
            str(output_path),
        ]
        if self.settings.xtts_use_cuda:
            command.extend(["--use_cuda", "true"])
        return command

    def _docker_command(
        self,
        *,
        text: str,
        output_path: Path,
        reference_paths: list[Path],
    ) -> list[str]:
        workspace_root = project_root().resolve()
        self._ensure_within_workspace(output_path, workspace_root)
        for path in reference_paths:
            self._ensure_within_workspace(path, workspace_root)

        command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{workspace_root}:/workspace",
            "-w",
            "/workspace",
        ]
        if self.settings.xtts_use_cuda:
            command.extend(["--gpus", "all"])
        command.append(self.settings.xtts_docker_image)
        command.extend(
            [
                "--text",
                text,
                "--model_name",
                self.settings.xtts_model_name,
                "--language_idx",
                self.settings.xtts_language,
                "--speaker_wav",
                *[self._to_container_path(path, workspace_root) for path in reference_paths],
                "--out_path",
                self._to_container_path(output_path, workspace_root),
            ]
        )
        if self.settings.xtts_use_cuda:
            command.extend(["--use_cuda", "true"])
        return command

    def _ensure_within_workspace(self, path: Path, workspace_root: Path) -> None:
        try:
            path.resolve().relative_to(workspace_root)
        except ValueError as exc:
            raise ConfigurationError(
                "XTTS docker mode expects the output and reference audio to live inside this repo."
            ) from exc

    def _to_container_path(self, path: Path, workspace_root: Path) -> str:
        relative = path.resolve().relative_to(workspace_root)
        return f"/workspace/{relative.as_posix()}"

    def _write_mock_wave(self, output_path: Path, duration_seconds: float) -> None:
        sample_rate = 16_000
        amplitude = 8_000
        frequency = 240.0
        total_frames = int(sample_rate * duration_seconds)
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            for index in range(total_frames):
                value = int(amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
                wav_file.writeframes(struct.pack("<h", value))
