from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class YoutubeKanaalError(Exception):
    """Base project exception."""


class ConfigurationError(YoutubeKanaalError):
    """Raised when configuration is invalid or incomplete."""


class ExternalDependencyError(YoutubeKanaalError):
    """Raised when an external tool or service is unavailable."""


@dataclass
class PipelineStageError(YoutubeKanaalError):
    """Raised when a pipeline stage fails with actionable context."""

    stage: str
    message: str
    probable_cause: str | None = None
    details_path: Path | None = None

    def __str__(self) -> str:
        segments = [f"[{self.stage}] {self.message}"]
        if self.probable_cause:
            segments.append(f"Probable cause: {self.probable_cause}")
        if self.details_path:
            segments.append(f"Inspect: {self.details_path}")
        return " | ".join(segments)
