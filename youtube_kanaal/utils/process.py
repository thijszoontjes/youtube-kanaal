from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from youtube_kanaal.exceptions import ExternalDependencyError, PipelineStageError


def command_exists(command: str) -> bool:
    return Path(command).exists() or shutil.which(command) is not None


def run_command(
    command: Sequence[str],
    *,
    timeout_seconds: int = 300,
    cwd: Path | None = None,
    input_text: str | None = None,
    stage: str,
) -> subprocess.CompletedProcess[str]:
    if not command:
        raise ValueError("Command cannot be empty.")
    if not command_exists(command[0]):
        raise ExternalDependencyError(f"Command not found: {command[0]}")
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            input=input_text,
            timeout=timeout_seconds,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.TimeoutExpired as exc:
        raise PipelineStageError(
            stage=stage,
            message=f"Command timed out after {timeout_seconds} seconds.",
            probable_cause="External tool is hanging or processing a very large input.",
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or "No command output captured."
        raise PipelineStageError(
            stage=stage,
            message=f"Command failed: {' '.join(command)}",
            probable_cause=details,
        ) from exc
