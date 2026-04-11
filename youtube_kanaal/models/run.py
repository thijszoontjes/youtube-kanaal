from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator


class RunStatus(str, Enum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class ShortRunRequest(BaseModel):
    upload: bool = False
    debug: bool = False
    preferred_topic: str | None = None
    preferred_bucket: str | None = None
    privacy_status: str | None = None
    scheduled_publish_at: datetime | None = None
    save_to_downloads: bool = True
    mock_mode: bool = False

    @field_validator("privacy_status")
    @classmethod
    def _validate_privacy_status(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {"private", "unlisted", "public"}:
            raise ValueError("Privacy status must be private, unlisted, or public.")
        return normalized

    @field_validator("scheduled_publish_at")
    @classmethod
    def _validate_scheduled_publish_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Scheduled publish time must include timezone information.")
        return value

    @model_validator(mode="after")
    def _validate_scheduling(self) -> "ShortRunRequest":
        if self.scheduled_publish_at is None:
            return self
        if not self.upload:
            raise ValueError("Scheduled publish time requires upload=True.")
        if self.privacy_status is None:
            self.privacy_status = "private"
            return self
        if self.privacy_status != "private":
            raise ValueError("Scheduled uploads must use privacy status 'private'.")
        return self


class BatchRequest(BaseModel):
    count: int = Field(default=3, ge=1, le=10)
    upload: bool = False
    debug: bool = False
    mock_mode: bool = False


class ShortRunResult(BaseModel):
    run_id: str
    title: str
    topic: str
    bucket: str
    duration_seconds: float = Field(ge=0)
    output_path: Path
    downloads_copy_path: Path | None = None
    uploaded: bool = False
    youtube_video_id: str | None = None
    privacy_status: str | None = None
    scheduled_publish_at: datetime | None = None
    log_path: Path
    metadata_path: Path


class HistoryEntry(BaseModel):
    run_id: str
    status: str
    topic: str | None = None
    title: str | None = None
    started_at: str
    duration_seconds: float | None = None
    output_path: str | None = None


class DoctorCheck(BaseModel):
    name: str
    status: str
    details: str
    action: str | None = None


class DoctorReport(BaseModel):
    checks: list[DoctorCheck]

    def all_ok(self) -> bool:
        return all(check.status == "ok" for check in self.checks)


class ValidationResult(BaseModel):
    run_id: str
    valid: bool
    checks: list[str]
    errors: list[str] = Field(default_factory=list)
