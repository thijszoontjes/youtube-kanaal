from __future__ import annotations

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
    response_path: Path | None = None
    uploaded: bool = False
