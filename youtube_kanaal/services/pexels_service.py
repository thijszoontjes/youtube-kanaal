from __future__ import annotations

import json
import logging
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


logger = logging.getLogger(__name__)


class PexelsService:
    """Pexels API adapter for searching and caching stock footage."""

    _BOOLEAN_SPLIT_RE = re.compile(r"\s+\b(?:or|and)\b\s+|\s*\|\|\s*|\s*\|\s*")
    _QUERY_CLEAN_RE = re.compile(r"[^a-zA-Z0-9\s&'\-]")
    _SEARCH_FATAL_STATUS_CODES = {401, 403}
    _GENERIC_QUERY_TOKENS = {
        "action",
        "animation",
        "broll",
        "cinematic",
        "city",
        "close",
        "crowd",
        "detail",
        "documentary",
        "drone",
        "footage",
        "landscape",
        "macro",
        "match",
        "motion",
        "nature",
        "ocean",
        "people",
        "scene",
        "slow",
        "sport",
        "stadium",
        "street",
        "technology",
        "underwater",
        "vertical",
        "video",
        "wildlife",
    }
    _SEARCH_FALLBACK_QUERIES = [
        "cinematic vertical footage",
        "nature landscape vertical",
        "city street vertical",
        "people daily life vertical",
    ]
    _TOPICAL_FALLBACKS = {
        "football": ["football match", "soccer stadium", "soccer ball"],
        "soccer": ["soccer match", "soccer stadium", "soccer ball"],
        "match": ["sports match", "stadium crowd", "athletes training"],
    }
    _SUBJECT_ALIASES = {
        "octopus": {"octopus", "octopuses", "pulpa", "pulp", "poulpe"},
    }
    _CONFLICTING_SUBJECT_CONTEXT = {
        "octopus": {"cooked", "cooking", "dish", "food", "grilled", "kitchen", "recipe"},
    }

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

        raw_payloads, candidates = self._collect_candidates(queries)
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

    def fetch_broll_clips(
        self,
        *,
        queries: list[str],
        max_clips: int,
        response_path: Path,
    ) -> list[VideoClipAsset]:
        if self.settings.mock_mode:
            clips = self._mock_clips(max_clips * 12.0)[:max_clips]
            write_json(response_path, [clip.model_dump(mode="json") for clip in clips])
            return clips
        if not self.settings.pexels_api_key:
            raise ConfigurationError("PEXELS_API_KEY is required for stock footage downloads.")

        raw_payloads, candidates = self._collect_candidates(queries)
        write_json(response_path, raw_payloads)

        chosen: list[VideoClipAsset] = []
        used_ids: set[str] = set()
        for clip in self._prioritized_candidates(candidates, queries):
            if clip.source_id in used_ids:
                continue
            if not self._prepare_clip_for_use(clip):
                continue
            chosen.append(clip)
            used_ids.add(clip.source_id)
            if len(chosen) >= max_clips:
                break
        if not chosen:
            raise PipelineStageError(
                stage="stock_video_download",
                message="Pexels returned no usable long-form B-roll clips.",
                probable_cause="Try a different topic, or inspect the saved API response.",
                details_path=response_path,
            )
        return chosen

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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def _search_unrestricted(self, query: str) -> dict[str, object]:
        """Search every orientation when portrait-only results miss the main subject."""

        response = self.client.get(
            "/videos/search",
            params={
                "query": query,
                "per_page": self.settings.pexels_results_per_query,
            },
        )
        response.raise_for_status()
        return response.json()

    def _collect_candidates(self, queries: list[str]) -> tuple[list[dict[str, object]], list[VideoClipAsset]]:
        raw_payloads: list[dict[str, object]] = []
        candidates: list[VideoClipAsset] = []
        searched_queries: set[str] = set()

        for original_query in queries:
            expanded_queries = self._expand_queries(original_query)
            for query in expanded_queries:
                searched_queries.add(query)
                self._search_and_collect(
                    query=query,
                    original_query=original_query,
                    raw_payloads=raw_payloads,
                    candidates=candidates,
                    fallback=False,
                )

        if not candidates:
            for query in self._fallback_queries(queries):
                if query in searched_queries:
                    continue
                searched_queries.add(query)
                self._search_and_collect(
                    query=query,
                    original_query=query,
                    raw_payloads=raw_payloads,
                    candidates=candidates,
                    fallback=True,
                )

        required_subjects = self._dominant_subject_tokens(queries)
        matching_ids = {
            clip.source_id
            for clip in candidates
            if self._matches_required_subject(clip, required_subjects)
        }
        if required_subjects and len(matching_ids) < 5:
            for query in queries[:5]:
                try:
                    payload = self._search_unrestricted(query)
                except httpx.HTTPStatusError as exc:
                    if self._is_fatal_search_error(exc):
                        raise
                    raw_payloads.append(
                        {
                            "query": query,
                            "original_query": query,
                            "fallback": True,
                            "orientation": "any",
                            "error": self._search_error_payload(exc),
                        }
                    )
                    continue
                except httpx.HTTPError as exc:
                    raw_payloads.append(
                        {
                            "query": query,
                            "original_query": query,
                            "fallback": True,
                            "orientation": "any",
                            "error": self._search_error_payload(exc),
                        }
                    )
                    continue
                raw_payloads.append(
                    {
                        "query": query,
                        "original_query": query,
                        "fallback": True,
                        "orientation": "any",
                        "response": payload,
                    }
                )
                candidates.extend(self._parse_results(query, payload))
                matching_ids = {
                    clip.source_id
                    for clip in candidates
                    if self._matches_required_subject(clip, required_subjects)
                }
                if len(matching_ids) >= 5:
                    break

        return raw_payloads, candidates

    def _search_and_collect(
        self,
        *,
        query: str,
        original_query: str,
        raw_payloads: list[dict[str, object]],
        candidates: list[VideoClipAsset],
        fallback: bool,
    ) -> None:
        try:
            payload = self._search(query)
        except httpx.HTTPStatusError as exc:
            if self._is_fatal_search_error(exc):
                raise
            raw_payloads.append(
                {
                    "query": query,
                    "original_query": original_query,
                    "fallback": fallback,
                    "error": self._search_error_payload(exc),
                }
            )
            logger.warning("Pexels search failed; trying remaining queries", extra={"query": query, "error": str(exc)})
            return
        except httpx.HTTPError as exc:
            raw_payloads.append(
                {
                    "query": query,
                    "original_query": original_query,
                    "fallback": fallback,
                    "error": self._search_error_payload(exc),
                }
            )
            logger.warning("Pexels search failed; trying remaining queries", extra={"query": query, "error": str(exc)})
            return

        raw_payloads.append(
            {"query": query, "original_query": original_query, "fallback": fallback, "response": payload}
        )
        candidates.extend(self._parse_results(query, payload))

    def _is_fatal_search_error(self, exc: httpx.HTTPStatusError) -> bool:
        return exc.response.status_code in self._SEARCH_FATAL_STATUS_CODES

    def _search_error_payload(self, exc: httpx.HTTPError) -> dict[str, object]:
        payload: dict[str, object] = {"type": exc.__class__.__name__, "message": str(exc)}
        if isinstance(exc, httpx.HTTPStatusError):
            payload["status_code"] = exc.response.status_code
            payload["url"] = str(exc.request.url)
        return payload

    def _fallback_queries(self, queries: list[str]) -> list[str]:
        fallback_queries: list[str] = []
        query_text = " ".join(queries).lower()
        for keyword, candidates in self._TOPICAL_FALLBACKS.items():
            if keyword in query_text:
                fallback_queries.extend(candidates)
        fallback_queries.extend(self._SEARCH_FALLBACK_QUERIES)
        return list(dict.fromkeys(fallback_queries))

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

        subject_tokens = query_tokens - self._GENERIC_QUERY_TOKENS
        subject_overlap = sum(
            1
            for token in subject_tokens
            if any(self._tokens_match(token, candidate) for candidate in haystack)
        )
        if subject_tokens and subject_overlap == 0:
            bonus -= 5.0
        elif subject_overlap >= 2:
            bonus += 1.8
        elif subject_overlap == 1:
            bonus += 0.7

        bonus += self._historical_relevance_adjustment(query_tokens=query_tokens, haystack=haystack)

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

    def _tokens_match(self, left: str, right: str) -> bool:
        if left == right:
            return True
        left_alias = self._canonical_subject_token(left)
        right_alias = self._canonical_subject_token(right)
        if left_alias == right_alias:
            return True
        left_stem = self._token_stem(left)
        right_stem = self._token_stem(right)
        return len(left_stem) >= 4 and left_stem == right_stem

    def _canonical_subject_token(self, token: str) -> str:
        normalized = token.lower()
        for canonical, aliases in self._SUBJECT_ALIASES.items():
            if normalized in aliases:
                return canonical
        return self._token_stem(normalized)

    def _token_stem(self, token: str) -> str:
        if token.endswith("es") and len(token) > 5:
            return token[:-2]
        return token.rstrip("s")

    def _dominant_subject_tokens(self, queries: list[str]) -> set[str]:
        counts: dict[str, int] = {}
        for query in queries:
            seen_in_query: set[str] = set()
            for token in re.findall(r"[a-z0-9]+", query.lower()):
                if len(token) <= 2 or token in self._GENERIC_QUERY_TOKENS:
                    continue
                canonical = self._canonical_subject_token(token)
                if canonical in seen_in_query:
                    continue
                counts[canonical] = counts.get(canonical, 0) + 1
                seen_in_query.add(canonical)
        if not counts:
            return set()
        highest = max(counts.values())
        minimum_repetition = max(2, (len(queries) + 3) // 4)
        if highest < minimum_repetition:
            return set()
        return {token for token, count in counts.items() if count == highest}

    def _matches_required_subject(self, clip: VideoClipAsset, required_subjects: set[str]) -> bool:
        if not required_subjects:
            return True
        source_tokens = set(re.findall(r"[a-z0-9]+", clip.source_url.lower()))
        for subject in required_subjects:
            if not any(self._tokens_match(subject, candidate) for candidate in source_tokens):
                continue
            conflicting = self._CONFLICTING_SUBJECT_CONTEXT.get(subject, set())
            if source_tokens & conflicting:
                return False
            return True
        return False

    def _historical_relevance_adjustment(self, *, query_tokens: set[str], haystack: set[str]) -> float:
        adjustment = 0.0
        titanic_terms = {"titanic", "shipwreck", "ship", "ocean", "liner", "iceberg", "lifeboat", "wreck"}
        if query_tokens & titanic_terms:
            matching_terms = {"titanic", "shipwreck", "ship", "ocean", "liner", "iceberg", "lifeboat", "wreck", "sea"}
            unrelated_terms = {
                "colosseum",
                "rome",
                "roman",
                "hamburg",
                "fountain",
                "fort",
                "temple",
                "castle",
                "pagoda",
                "warehouse",
            }
            if haystack & matching_terms:
                adjustment += 2.0
            if haystack & unrelated_terms:
                adjustment -= 3.0
        if {"museum", "artifact"} & query_tokens and {"museum", "artifact", "archive", "history"} & haystack:
            adjustment += 1.2
        return adjustment

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
        required_subjects = self._dominant_subject_tokens(queries)
        viable_candidates = [
            clip
            for clip in candidates
            if clip.score >= 4.0 and self._matches_required_subject(clip, required_subjects)
        ]
        for clip in self._prioritized_candidates(viable_candidates, queries):
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
