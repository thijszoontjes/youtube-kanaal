from __future__ import annotations

import logging
import platform
from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.utils.files import write_text
from youtube_kanaal.utils.process import command_exists, run_command


class MacOSSayService:
    """Use the built-in macOS speech synthesizer with an explicitly chosen voice."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def runtime_ready(self) -> tuple[bool, str | None]:
        if platform.system() != "Darwin":
            return False, "The built-in say voice engine is available only on macOS."
        binary = self.settings.macos_say_binary
        if not command_exists(binary):
            return False, f"macOS say executable not found: {binary}"
        try:
            result = run_command([binary, "-v", "?"], timeout_seconds=15, stage="narration_generation")
        except PipelineStageError as exc:
            return False, exc.probable_cause or str(exc)
        installed_voices = {
            line.split()[0]
            for line in f"{result.stdout}\n{result.stderr}".splitlines()
            if line.split()
        }
        if self.settings.macos_say_voice not in installed_voices:
            return False, f"macOS say voice {self.settings.macos_say_voice!r} is not installed."
        return True, None

    def synthesize(
        self,
        *,
        text: str,
        output_path: Path,
        logger: logging.Logger | None = None,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        text_path = output_path.with_name(f"{output_path.stem}_say_input.txt")
        write_text(text_path, text)
        command = [
            self.settings.macos_say_binary,
            "-v",
            self.settings.macos_say_voice,
            "-r",
            str(self.settings.macos_say_rate),
            "-o",
            str(output_path),
            "-f",
            str(text_path),
        ]
        if logger:
            logger.info(
                "macos_say_synthesis: start",
                extra={
                    "stage": "narration_generation",
                    "voice": self.settings.macos_say_voice,
                    "rate_words_per_minute": self.settings.macos_say_rate,
                },
            )
        try:
            run_command(command, timeout_seconds=600, stage="narration_generation")
        except Exception as exc:
            if isinstance(exc, PipelineStageError):
                raise
            raise PipelineStageError(
                stage="narration_generation",
                message="macOS say synthesis failed.",
                probable_cause=str(exc),
            ) from exc
        finally:
            text_path.unlink(missing_ok=True)
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise PipelineStageError(
                stage="narration_generation",
                message="macOS say did not produce an audio file.",
                probable_cause=f"Expected a non-empty audio file at {output_path}.",
            )
        if logger:
            logger.info(
                "macos_say_synthesis: finish",
                extra={
                    "stage": "narration_generation",
                    "output_path": str(output_path),
                    "size_bytes": output_path.stat().st_size,
                },
            )
        return output_path
