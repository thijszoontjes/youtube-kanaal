from __future__ import annotations

import re
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import ConfigurationError, PipelineStageError
from youtube_kanaal.models.assets import VideoClipAsset
from youtube_kanaal.utils.files import write_json
from youtube_kanaal.utils.subtitles import ideal_clip_count


class PexelsService:
    """Pexels API adapter for searching and caching stock footage."""

    _BOOLEAN_SPLIT_RE = re.compile(r"\s+\b(?:or|and)\b\s+|\s*\|\|\s*|\s*\|\s*")
    _QUERY_CLEAN_RE = re.compile(r"[^a-zA-Z0-9\s&'\-]")

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            base_url="https://api.pexels.com",
            timeout=self.settings.network_timeout_seconds,
            headers={"Authorization": self.settings.pexels_api_key or ""},
        )

    def validate_credentials(self) -> bool:
        if self.settings.mock_mode:
            return True
        if not self.settings.pexels_api_key:
            return False
        try:
            response = self.client.get(
                "/videos/search",
                params={"query": "ocean", "per_page": 1, "orientation": "portrait"},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return False
        return True

    def fetch_clips(
        self,
        *,
        queries: list[str],
        target_duration_seconds: float,
        response_path: Path,
    ) -> list[VideoClipAsset]:
        if self.settings.mock_mode:
            clips = self._mock_clips(target_duration_seconds)
            write_json(response_path, [clip.model_dump(mode="json") for clip in clips])
            return clips
        if not self.settings.pexels_api_key:
            raise ConfigurationError("PEXELS_API_KEY is required for stock footage downloads.")

        raw_payloads: list[dict[str, object]] = []
        candidates: list[VideoClipAsset] = []
        for original_query in queries:
            for query in self._expand_queries(original_query):
                payload = self._search(query)
                raw_payloads.append({"query": query, "original_query": original_query, "response": payload})
                candidates.extend(self._parse_results(query, payload))
        write_json(response_path, raw_payloads)
        selected = self._select_and_download(candidates, target_duration_seconds)
        if not selected:
            raise PipelineStageError(
                stage="stock_video_download",
                message="Pexels returned no usable clips.",
                probable_cause="Try a different topic, or inspect the saved API response.",
                details_path=response_path,
            )
        return selected

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def _search(self, query: str) -> dict[str, object]:
        response = self.client.get(
            "/videos/search",
            params={
                "query": query,
                "per_page": self.settings.pexels_results_per_query,
                "orientation": "portrait",
            },
        )
        response.raise_for_status()
        return response.json()

    def _expand_queries(self, query: str) -> list[str]:
        parts = self._BOOLEAN_SPLIT_RE.split(query.strip())
        cleaned_parts: list[str] = []
        for part in parts:
            cleaned = self._clean_query(part)
            if cleaned and cleaned not in cleaned_parts:
                cleaned_parts.append(cleaned)
        fallback = self._clean_query(query)
        return cleaned_parts or ([fallback] if fallback else [])

    def _clean_query(self, query: str) -> str:
        cleaned = query.replace('"', " ").replace("“", " ").replace("”", " ")
        cleaned = self._QUERY_CLEAN_RE.sub(" ", cleaned)
        return " ".join(cleaned.split()).strip()

    def _parse_results(self, query: str, payload: dict[str, object]) -> list[VideoClipAsset]:
        clips: list[VideoClipAsset] = []
        for video in payload.get("videos", []):
            if not isinstance(video, dict):
                continue
            best_file = self._choose_best_file(video)
            if not best_file:
                continue
            duration = float(video.get("duration") or 0)
            width = int(best_file.get("width") or video.get("width") or 1)
            height = int(best_file.get("height") or video.get("height") or 1)
            score = self._score_clip(duration_seconds=duration, width=width, height=height)
            source_id = str(video.get("id"))
            cache_path = self.settings.cache_dir / "pexels" / f"{source_id}.mp4"
            clips.append(
                VideoClipAsset(
                    source_id=source_id,
                    query=query,
                    source_url=str(video.get("url") or ""),
                    download_url=str(best_file.get("link") or ""),
                    local_path=cache_path,
                    duration_seconds=duration,
                    width=width,
                    height=height,
                    score=score,
                    attribution=(video.get("user") or {}).get("name") if isinstance(video.get("user"), dict) else None,
                )
            )
        return clips

    def _choose_best_file(self, video: dict[str, object]) -> dict[str, object] | None:
        files = video.get("video_files", [])
        if not isinstance(files, list):
            return None
        ranked = sorted(
            [item for item in files if isinstance(item, dict)],
            key=lambda item: (
                1 if int(item.get("height") or 0) >= int(item.get("width") or 0) else 0,
                int(item.get("height") or 0),
                int(item.get("width") or 0),
            ),
            reverse=True,
        )
        return ranked[0] if ranked else None

    def _score_clip(self, *, duration_seconds: float, width: int, height: int) -> float:
        orientation_bonus = 2.0 if height >= width else 0.5
        duration_bonus = max(0.5, min(duration_seconds, 18) / 6)
        resolution_bonus = min(height / max(width, 1), 2.0)
        return round(orientation_bonus + duration_bonus + resolution_bonus, 2)

    def _select_and_download(
        self,
        candidates: list[VideoClipAsset],
        target_duration_seconds: float,
    ) -> list[VideoClipAsset]:
        chosen: list[VideoClipAsset] = []
        used_ids: set[str] = set()
        cumulative = 0.0
        desired_count = ideal_clip_count(target_duration_seconds)
        for clip in sorted(candidates, key=lambda item: item.score, reverse=True):
            if clip.source_id in used_ids:
                continue
            self._download_clip(clip)
            chosen.append(clip)
            used_ids.add(clip.source_id)
            cumulative += min(clip.duration_seconds, max(target_duration_seconds / desired_count, 4.0))
            if len(chosen) >= desired_count and cumulative >= target_duration_seconds:
                break
        return chosen

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def _download_clip(self, clip: VideoClipAsset) -> None:
        clip.local_path.parent.mkdir(parents=True, exist_ok=True)
        if clip.local_path.exists():
            return
        response = self.client.get(clip.download_url)
        response.raise_for_status()
        clip.local_path.write_bytes(response.content)

    def _mock_clips(self, target_duration_seconds: float) -> list[VideoClipAsset]:
        clip_count = ideal_clip_count(target_duration_seconds)
        clips: list[VideoClipAsset] = []
        for index in range(clip_count):
            local_path = self.settings.cache_dir / "pexels" / f"mock-{index}.mp4"
            local_path.parent.mkdir(parents=True, exist_ok=True)
            if not local_path.exists():
                local_path.write_text("mock video clip", encoding="utf-8")
            clips.append(
                VideoClipAsset(
                    source_id=f"mock-{index}",
                    query="mock",
                    source_url="https://example.invalid/mock",
                    download_url="https://example.invalid/mock.mp4",
                    local_path=local_path,
                    duration_seconds=max(target_duration_seconds / clip_count, 4.0),
                    width=1080,
                    height=1920,
                    score=5.0,
                    attribution="Mock Author",
                )
            )
        return clips
