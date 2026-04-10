from __future__ import annotations

import json
import logging
import math
import os
import struct
import wave
from pathlib import Path

from youtube_kanaal.config import Settings, project_root
from youtube_kanaal.exceptions import ConfigurationError, PipelineStageError
from youtube_kanaal.utils.process import command_exists, run_command
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

    def runtime_ready(self) -> tuple[bool, str | None]:
        if self.settings.mock_mode:
            return True, None
        if self.settings.xtts_runtime == "docker":
            if not command_exists("docker"):
                return False, "Docker is not installed or not on PATH."
            try:
                run_command(
                    ["docker", "image", "inspect", self.settings.xtts_docker_image],
                    timeout_seconds=20,
                    stage="narration_generation",
                )
            except PipelineStageError as exc:
                reason = exc.probable_cause or exc.message
                return False, (
                    f"XTTS Docker image is not ready locally: {self.settings.xtts_docker_image}. {reason}"
                )
            return True, None
        if not command_exists(self.settings.xtts_binary):
            return False, f"XTTS binary not found: {self.settings.xtts_binary}"
        try:
            run_command(
                [self.settings.xtts_binary, "--version"],
                timeout_seconds=120,
                stage="narration_generation",
            )
        except Exception as exc:
            if isinstance(exc, PipelineStageError):
                reason = exc.probable_cause or exc.message
            else:
                reason = str(exc)
            return False, f"XTTS binary failed health check: {reason}"
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
            self._validate_audio_file(
                output_path,
                message="XTTS mock mode did not create the expected narration WAV.",
            )
            return output_path

        reference_paths = self.prepare_reference_audio(logger=logger)
        command = self._build_command(
            text=text,
            output_path=output_path,
            reference_paths=reference_paths,
        )
        if logger:
            logger.info(
                "xtts_synthesis: start",
                extra={
                    "stage": "narration_generation",
                    "engine": "xtts",
                    "xtts_runtime": self.settings.xtts_runtime,
                    "output_path": str(output_path),
                    "reference_paths": [str(path) for path in reference_paths],
                },
            )
        try:
            run_command(
                command,
                timeout_seconds=self.settings.xtts_timeout_seconds,
                env=self._runtime_environment(),
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
        self._validate_audio_file(
            output_path,
            message="XTTS did not create the expected narration WAV.",
        )
        if logger:
            logger.info(
                "xtts_synthesis: finish",
                extra={
                    "stage": "narration_generation",
                    "engine": "xtts",
                    "output_path": str(output_path),
                    "size_bytes": output_path.stat().st_size,
                },
            )
        return output_path

    def discover_reference_sources(self, *, logger: logging.Logger | None = None) -> list[Path]:
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

        selected = existing_candidates[: self.settings.xtts_max_reference_clips]
        if logger:
            logger.info(
                "xtts_reference_discovery: finish",
                extra={
                    "stage": "narration_generation",
                    "engine": "xtts",
                    "configured_reference_path": (
                        str(self.settings.xtts_speaker_wav_path)
                        if self.settings.xtts_speaker_wav_path
                        else None
                    ),
                    "configured_reference_dir": (
                        str(self.settings.xtts_speaker_wav_dir)
                        if self.settings.xtts_speaker_wav_dir
                        else None
                    ),
                    "discovered_reference_sources": [str(path) for path in selected],
                    "discovered_reference_count": len(selected),
                },
            )
        return selected

    def describe_reference_sources(self) -> list[dict[str, object]]:
        return [self._describe_reference_source(path) for path in self.discover_reference_sources()]

    def prepare_reference_audio(self, *, logger: logging.Logger | None = None) -> list[Path]:
        source_paths = self.discover_reference_sources(logger=logger)
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
            if logger:
                logger.info(
                    "xtts_reference_preprocessing: source",
                    extra={
                        "stage": "narration_generation",
                        "engine": "xtts",
                        **self._describe_reference_source(source_path),
                    },
                )
                logger.info(
                    "xtts_reference_preprocessing: convert",
                    extra={
                        "stage": "narration_generation",
                        "engine": "xtts",
                        "source_path": str(source_path),
                        "prepared_path": str(prepared_path),
                        "target_sample_rate": 24000,
                        "target_channels": 1,
                        "max_seconds": self.settings.xtts_reference_max_seconds,
                    },
                )
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
            self._validate_audio_file(
                prepared_path,
                message="FFmpeg did not create the prepared XTTS reference WAV.",
            )
            prepared_paths.append(prepared_path)
            if logger:
                logger.info(
                    "xtts_reference_preprocessing: finish",
                    extra={
                        "stage": "narration_generation",
                        "engine": "xtts",
                        "source_path": str(source_path),
                        "prepared_path": str(prepared_path),
                        "prepared_size_bytes": prepared_path.stat().st_size,
                    },
                )
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

    def _runtime_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        if self.settings.coqui_tos_agreed:
            environment["COQUI_TOS_AGREED"] = "1"
        ffmpeg_dir = Path(self.settings.ffmpeg_binary).expanduser().resolve().parent
        if ffmpeg_dir.exists():
            current_path = environment.get("PATH", "")
            ffmpeg_dir_str = str(ffmpeg_dir)
            if ffmpeg_dir_str not in current_path.split(os.pathsep):
                environment["PATH"] = (
                    f"{ffmpeg_dir_str}{os.pathsep}{current_path}" if current_path else ffmpeg_dir_str
                )
        return environment

    def _describe_reference_source(self, path: Path) -> dict[str, object]:
        payload: dict[str, object] = {
            "source_path": str(path),
            "source_extension": path.suffix.lower(),
            "source_size_bytes": path.stat().st_size if path.exists() else None,
        }
        ffprobe_binary = self._ffprobe_binary()
        if not command_exists(ffprobe_binary):
            payload["probe_status"] = "ffprobe_missing"
            return payload
        try:
            result = run_command(
                [
                    ffprobe_binary,
                    "-v",
                    "error",
                    "-show_streams",
                    "-select_streams",
                    "a:0",
                    "-of",
                    "json",
                    str(path),
                ],
                timeout_seconds=30,
                stage="narration_generation",
            )
            stream = (json.loads(result.stdout or "{}").get("streams") or [{}])[0]
            payload.update(
                {
                    "probe_status": "ok",
                    "codec_name": stream.get("codec_name"),
                    "sample_rate": int(stream["sample_rate"]) if stream.get("sample_rate") else None,
                    "channels": int(stream["channels"]) if stream.get("channels") else None,
                    "duration_seconds": float(stream["duration"]) if stream.get("duration") else None,
                }
            )
        except Exception as exc:
            payload["probe_status"] = "failed"
            payload["probe_error"] = str(exc)
        return payload

    def _ffprobe_binary(self) -> str:
        ffmpeg_path = Path(self.settings.ffmpeg_binary)
        if ffmpeg_path.exists():
            return str(ffmpeg_path.with_name("ffprobe.exe" if ffmpeg_path.suffix.lower() == ".exe" else "ffprobe"))
        return "ffprobe"

    def _validate_audio_file(self, output_path: Path, *, message: str) -> None:
        if not output_path.exists():
            raise PipelineStageError(
                stage="narration_generation",
                message=message,
                probable_cause=f"Expected output file is missing: {output_path}",
            )
        if output_path.stat().st_size == 0:
            raise PipelineStageError(
                stage="narration_generation",
                message=message,
                probable_cause=f"Output file is empty: {output_path}",
            )

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
