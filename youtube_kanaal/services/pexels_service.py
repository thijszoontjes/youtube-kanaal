from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import ConfigurationError, PipelineStageError
from youtube_kanaal.models.assets import VideoClipAsset
from youtube_kanaal.utils.files import write_json
from youtube_kanaal.utils.process import command_exists, run_command
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
        selected = self._select_and_download(candidates, target_duration_seconds, queries=queries)
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
            source_url = str(video.get("url") or "")
            score = self._score_clip(
                duration_seconds=duration,
                width=width,
                height=height,
                query=query,
                source_url=source_url,
            )
            source_id = str(video.get("id"))
            cache_path = self.settings.cache_dir / "pexels" / f"{source_id}.mp4"
            clips.append(
                VideoClipAsset(
                    source_id=source_id,
                    query=query,
                    source_url=source_url,
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

    def _score_clip(self, *, duration_seconds: float, width: int, height: int, query: str, source_url: str) -> float:
        orientation_bonus = 2.0 if height >= width else 0.5
        duration_bonus = max(0.5, min(duration_seconds, 18) / 6)
        resolution_bonus = min(height / max(width, 1), 2.0)
        relevance_bonus = self._relevance_bonus(query=query, source_url=source_url)
        return round(max(0.0, orientation_bonus + duration_bonus + resolution_bonus + relevance_bonus), 2)

    def _relevance_bonus(self, *, query: str, source_url: str) -> float:
        query_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", query.lower())
            if len(token) > 2 and token not in {"close", "macro", "drone", "slow", "motion", "animation"}
        }
        if not query_tokens:
            return 0.0
        haystack = set(re.findall(r"[a-z0-9]+", source_url.lower()))
        overlap = len(query_tokens & haystack)
        bonus = 0.0
        if overlap >= 2:
            bonus += 2.0
        elif overlap == 1:
            bonus += 0.8
        else:
            bonus -= 1.2

        space_terms = {"saturn", "planet", "space", "moon", "solar", "astronomy", "orbit", "galaxy", "star", "telescope"}
        space_hits = len(query_tokens & space_terms)
        if space_hits:
            astronomy_tokens = {"saturn", "space", "moon", "solar", "astronomy", "orbit", "galaxy", "star", "earth", "mars", "venus", "pluto", "eclipse", "nebula"}
            unrelated_tokens = {
                "woman",
                "women",
                "man",
                "people",
                "person",
                "fingers",
                "laptop",
                "urban",
                "street",
                "city",
                "writing",
                "brain",
                "desk",
                "office",
                "bus",
                "tiny",
                "landscape",
                "cityscape",
                "learning",
            }
            astronomy_overlap = len(haystack & astronomy_tokens)
            planet_overlap = 1 if "planet" in haystack else 0
            unrelated_overlap = len(haystack & unrelated_tokens)
            if astronomy_overlap:
                bonus += 2.6
            elif planet_overlap:
                bonus += 0.5
            else:
                bonus -= 2.4
            if unrelated_overlap:
                bonus -= 2.2
        return round(bonus, 2)

    def _select_and_download(
        self,
        candidates: list[VideoClipAsset],
        target_duration_seconds: float,
        *,
        queries: list[str],
    ) -> list[VideoClipAsset]:
        chosen: list[VideoClipAsset] = []
        used_ids: set[str] = set()
        cumulative = 0.0
        desired_count = ideal_clip_count(target_duration_seconds)
        for clip in self._prioritized_candidates(candidates, queries):
            if clip.source_id in used_ids:
                continue
            if not self._prepare_clip_for_use(clip):
                continue
            chosen.append(clip)
            used_ids.add(clip.source_id)
            cumulative += min(clip.duration_seconds, max(target_duration_seconds / desired_count, 4.0))
            if len(chosen) >= desired_count and cumulative >= target_duration_seconds:
                break
        return chosen

    def _prioritized_candidates(
        self,
        candidates: list[VideoClipAsset],
        queries: list[str],
    ) -> list[VideoClipAsset]:
        grouped: dict[str, list[VideoClipAsset]] = {}
        for clip in sorted(candidates, key=lambda item: item.score, reverse=True):
            grouped.setdefault(clip.query, []).append(clip)

        prioritized: list[VideoClipAsset] = []
        used_ids: set[str] = set()

        for query in queries:
            for clip in grouped.get(query, []):
                if clip.source_id in used_ids:
                    continue
                prioritized.append(clip)
                used_ids.add(clip.source_id)
                break

        for clip in sorted(candidates, key=lambda item: item.score, reverse=True):
            if clip.source_id in used_ids:
                continue
            prioritized.append(clip)
            used_ids.add(clip.source_id)
        return prioritized

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def _download_clip(self, clip: VideoClipAsset) -> None:
        clip.local_path.parent.mkdir(parents=True, exist_ok=True)
        response = self.client.get(clip.download_url)
        response.raise_for_status()
        temp_path = clip.local_path.with_suffix(f"{clip.local_path.suffix}.part")
        temp_path.write_bytes(response.content)
        temp_path.replace(clip.local_path)

    def _prepare_clip_for_use(self, clip: VideoClipAsset) -> bool:
        clip.local_path.parent.mkdir(parents=True, exist_ok=True)
        if clip.local_path.exists() and self._is_valid_clip_file(clip.local_path):
            return True
        if clip.local_path.exists():
            clip.local_path.unlink(missing_ok=True)

        try:
            self._download_clip(clip)
        except httpx.HTTPError:
            return False

        if self._is_valid_clip_file(clip.local_path):
            return True

        clip.local_path.unlink(missing_ok=True)
        return False

    def _is_valid_clip_file(self, path: Path) -> bool:
        if not path.exists() or path.stat().st_size < 1024:
            return False

        ffprobe_binary = self._ffprobe_binary()
        if not command_exists(ffprobe_binary):
            return True

        try:
            result = run_command(
                [
                    ffprobe_binary,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height,codec_name",
                    "-of",
                    "json",
                    str(path),
                ],
                timeout_seconds=30,
                stage="stock_video_download",
            )
        except PipelineStageError:
            return False

        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams", [])
        if not streams:
            return False
        stream = streams[0]
        return bool(int(stream.get("width") or 0) > 0 and int(stream.get("height") or 0) > 0)

    def _ffprobe_binary(self) -> str:
        ffmpeg_path = Path(self.settings.ffmpeg_binary)
        if ffmpeg_path.exists():
            suffix = "ffprobe.exe" if ffmpeg_path.suffix.lower() == ".exe" else "ffprobe"
            return str(ffmpeg_path.with_name(suffix))
        return "ffprobe"

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
