from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field


class NarrationAsset(BaseModel):
    raw_path: Path
    normalized_path: Path
    duration_seconds: float = Field(ge=0)


class SubtitleAsset(BaseModel):
    srt_path: Path
    vtt_path: Path | None = None
    ass_path: Path | None = None


class SoundDesignAsset(BaseModel):
    mixed_path: Path
    applied: bool = False
    duration_seconds: float = Field(ge=0)
    effect_count: int = Field(default=0, ge=0)
    stem_paths: list[Path] = Field(default_factory=list)
    fallback_reason: str | None = None


class VideoClipAsset(BaseModel):
    source_id: str
    query: str
    source_url: str
    download_url: str
    local_path: Path
    duration_seconds: float = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    score: float = Field(default=0, ge=0)
    attribution: str | None = None

    @property
    def is_portrait(self) -> bool:
        return self.height >= self.width


class AssetPlanSegment(BaseModel):
    clip_path: Path
    duration_seconds: float = Field(ge=0.5)
    reason: str


class AssetPlan(BaseModel):
    segments: list[AssetPlanSegment] = Field(min_length=1)
    total_duration_seconds: float = Field(ge=0.5)


class UploadMetadata(BaseModel):
    youtube_video_id: str | None = None
    privacy_status: str
    scheduled_publish_at: datetime | None = None
    response_path: Path | None = None
    uploaded: bool = False
