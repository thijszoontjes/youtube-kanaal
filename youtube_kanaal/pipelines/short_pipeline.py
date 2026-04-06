from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from youtube_kanaal.config import Settings
from youtube_kanaal.db import Database
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.logging_config import LoggingBundle, configure_run_logging
from youtube_kanaal.models import (
    AssetPlan,
    AssetPlanSegment,
    GeneratedShort,
    NarrationAsset,
    ShortRunRequest,
    ShortRunResult,
    SubtitleAsset,
    TopicChoice,
    UploadMetadata,
    ValidationResult,
    VideoClipAsset,
)
from youtube_kanaal.services.ffmpeg_service import FFmpegService
from youtube_kanaal.services.ollama_service import OllamaService
from youtube_kanaal.services.pexels_service import PexelsService
from youtube_kanaal.services.piper_service import PiperService
from youtube_kanaal.services.whisper_service import WhisperService
from youtube_kanaal.services.youtube_service import YouTubeService
from youtube_kanaal.utils.files import copy_collision_safe, ensure_directory, safe_slug, write_json
from youtube_kanaal.utils.similarity import is_near_duplicate, normalize_for_similarity


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_run_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{uuid4().hex[:8]}"


@dataclass
class RunArtifacts:
    run_id: str
    run_dir: Path
    prompts_dir: Path
    responses_dir: Path
    audio_dir: Path
    subtitles_dir: Path
    assets_dir: Path
    video_dir: Path
    metadata_dir: Path

    @classmethod
    def create(cls, settings: Settings, run_id: str) -> "RunArtifacts":
        run_dir = ensure_directory(settings.output_dir / run_id)
        return cls(
            run_id=run_id,
            run_dir=run_dir,
            prompts_dir=ensure_directory(run_dir / "prompts"),
            responses_dir=ensure_directory(run_dir / "responses"),
            audio_dir=ensure_directory(run_dir / "audio"),
            subtitles_dir=ensure_directory(run_dir / "subtitles"),
            assets_dir=ensure_directory(run_dir / "assets"),
            video_dir=ensure_directory(run_dir / "video"),
            metadata_dir=ensure_directory(run_dir / "metadata"),
        )


@dataclass
class PipelineRuntime:
    settings: Settings
    request: ShortRunRequest
    run_id: str
    artifacts: RunArtifacts
    logging_bundle: LoggingBundle
    logger: logging.Logger
    stage_summaries: dict[str, dict[str, object]] = field(default_factory=dict)


class ShortPipeline:
    """End-to-end Short generation pipeline."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        *,
        ollama_service: OllamaService | None = None,
        piper_service: PiperService | None = None,
        whisper_service: WhisperService | None = None,
        pexels_service: PexelsService | None = None,
        ffmpeg_service: FFmpegService | None = None,
        youtube_service: YouTubeService | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.ollama = ollama_service or OllamaService(settings)
        self.piper = piper_service or PiperService(settings)
        self.whisper = whisper_service or WhisperService(settings)
        self.pexels = pexels_service or PexelsService(settings)
        self.ffmpeg = ffmpeg_service or FFmpegService(settings)
        self.youtube = youtube_service or YouTubeService(settings)

    def run(self, request: ShortRunRequest) -> ShortRunResult:
        run_id = _new_run_id()
        artifacts = RunArtifacts.create(self.settings, run_id)
        logging_bundle = configure_run_logging(run_id, self.settings.logs_dir, debug=request.debug or self.settings.app_debug)
        runtime = PipelineRuntime(
            settings=self.settings,
            request=request,
            run_id=run_id,
            artifacts=artifacts,
            logging_bundle=logging_bundle,
            logger=logging_bundle.logger,
        )

        started_at = _utc_now_iso()
        self.database.insert_run(
            run_id=run_id,
            status="running",
            started_at=started_at,
            log_path=str(logging_bundle.human_log_path),
            upload_requested=request.upload,
            mock_mode=request.mock_mode or self.settings.mock_mode,
        )
        runtime.logger.info("Run started", extra={"run_id": run_id, "started_at": started_at})

        try:
            topic = self.select_topic(runtime)
            content = self.generate_content(runtime, topic)
            narration = self.generate_narration(runtime, content)
            subtitles = self.generate_subtitles(runtime, content, narration)
            clips = self.download_stock_video(runtime, topic, content, narration)
            plan = self.plan_assets(runtime, clips, narration)
            final_video_path = self.render_video(runtime, plan, narration, subtitles, content)
            validation_payload = self.validate_output(runtime, final_video_path)
            downloads_copy = self.export_to_downloads(runtime, final_video_path, content)
            upload_metadata = self.upload_if_requested(runtime, final_video_path, content)
            result = self.persist_run(
                runtime=runtime,
                topic=topic,
                content=content,
                narration=narration,
                subtitles=subtitles,
                clips=clips,
                final_video_path=final_video_path,
                downloads_copy_path=downloads_copy,
                validation_payload=validation_payload,
                upload_metadata=upload_metadata,
                started_at=started_at,
            )
            runtime.logger.info("Run completed", extra={"run_id": run_id, "output_path": str(final_video_path)})
            return result
        except Exception as exc:
            stage_name = exc.stage if isinstance(exc, PipelineStageError) else "pipeline"
            message = str(exc)
            completed_at = _utc_now_iso()
            runtime.logger.exception(
                "Run failed",
                extra={"run_id": run_id, "stage": stage_name, "error": message},
            )
            self.database.mark_run_failed(
                run_id=run_id,
                stage=stage_name,
                error_message=message,
                completed_at=completed_at,
            )
            raise

    def select_topic(self, runtime: PipelineRuntime) -> TopicChoice:
        recent_topics = self.database.recent_topics(limit=100)
        with self._stage(runtime, "topic_selection", {"recent_topics": len(recent_topics)}):
            if runtime.request.preferred_topic:
                topic = self._preferred_topic(runtime.request.preferred_topic, runtime.request.preferred_bucket)
            else:
                topic = self.ollama.choose_topic(
                    excluded_topics=recent_topics,
                    prompt_path=runtime.artifacts.prompts_dir / "topic_selection.txt",
                    response_path=runtime.artifacts.responses_dir / "topic_selection.json",
                )
                if is_near_duplicate(topic.topic, recent_topics, self.settings.similarity_threshold):
                    topic = self._fallback_topic_excluding(recent_topics)
            runtime.stage_summaries["topic_selection"] = topic.model_dump(mode="json")
            return topic

    def generate_content(self, runtime: PipelineRuntime, topic: TopicChoice) -> GeneratedShort:
        recent_titles = self.database.recent_titles(limit=100)
        with self._stage(runtime, "content_generation", {"topic": topic.topic, "recent_titles": len(recent_titles)}):
            last_error: Exception | None = None
            for _ in range(self.settings.retry_attempts):
                content = self.ollama.generate_short_content(
                    topic=topic,
                    excluded_titles=recent_titles,
                    prompt_path=runtime.artifacts.prompts_dir / "content_generation.txt",
                    response_path=runtime.artifacts.responses_dir / "content_generation.json",
                )
                if not is_near_duplicate(content.title, recent_titles, self.settings.similarity_threshold):
                    runtime.stage_summaries["content_generation"] = content.model_dump(mode="json")
                    return content
                if runtime.request.preferred_topic:
                    retitled = self._retitle_requested_topic_content(content, recent_titles)
                    if retitled is not None:
                        runtime.stage_summaries["content_generation"] = retitled.model_dump(mode="json")
                        return retitled
                recent_titles.append(content.title)
                last_error = PipelineStageError(
                    stage="content_generation",
                    message="Generated title was too similar to recent history.",
                    probable_cause="Ollama produced a near-duplicate title repeatedly.",
                )
            raise last_error or PipelineStageError(
                stage="content_generation",
                message="Failed to generate content.",
            )

    def generate_narration(self, runtime: PipelineRuntime, content: GeneratedShort) -> NarrationAsset:
        with self._stage(runtime, "narration_generation", {"topic": content.topic, "title": content.title}):
            raw_path = runtime.artifacts.audio_dir / "narration_raw.wav"
            normalized_path = runtime.artifacts.audio_dir / "narration.wav"
            self.piper.synthesize(text=content.narration, output_path=raw_path)
            self.ffmpeg.normalize_audio(input_path=raw_path, output_path=normalized_path)
            duration_seconds = self.ffmpeg.audio_duration_seconds(normalized_path)
            asset = NarrationAsset(
                raw_path=raw_path,
                normalized_path=normalized_path,
                duration_seconds=duration_seconds,
            )
            runtime.stage_summaries["narration_generation"] = asset.model_dump(mode="json")
            return asset

    def generate_subtitles(
        self,
        runtime: PipelineRuntime,
        content: GeneratedShort,
        narration: NarrationAsset,
    ) -> SubtitleAsset:
        with self._stage(runtime, "subtitle_generation", {"audio_seconds": narration.duration_seconds}):
            subtitles = self.whisper.generate_subtitles(
                audio_path=narration.normalized_path,
                subtitle_text=content.subtitle_text,
                output_base_path=runtime.artifacts.subtitles_dir / "captions",
                duration_seconds=narration.duration_seconds,
            )
            runtime.stage_summaries["subtitle_generation"] = subtitles.model_dump(mode="json")
            return subtitles

    def download_stock_video(
        self,
        runtime: PipelineRuntime,
        topic: TopicChoice,
        content: GeneratedShort,
        narration: NarrationAsset,
    ) -> list[VideoClipAsset]:
        queries = self._build_video_queries(topic, content)
        with self._stage(runtime, "stock_video_download", {"queries": queries}):
            clips = self.pexels.fetch_clips(
                queries=queries,
                target_duration_seconds=narration.duration_seconds,
                response_path=runtime.artifacts.responses_dir / "pexels_search.json",
            )
            runtime.stage_summaries["stock_video_download"] = {
                "clip_count": len(clips),
                "queries": queries,
            }
            return clips

    def plan_assets(
        self,
        runtime: PipelineRuntime,
        clips: list[VideoClipAsset],
        narration: NarrationAsset,
    ) -> AssetPlan:
        with self._stage(runtime, "asset_planning", {"clip_count": len(clips), "duration": narration.duration_seconds}):
            segment_duration = narration.duration_seconds / max(len(clips), 1)
            plan = AssetPlan(
                segments=[
                    AssetPlanSegment(
                        clip_path=clip.local_path,
                        duration_seconds=max(2.0, min(segment_duration, clip.duration_seconds)),
                        reason=f"Selected for query: {clip.query}",
                    )
                    for clip in clips
                ],
                total_duration_seconds=narration.duration_seconds,
            )
            runtime.stage_summaries["asset_planning"] = plan.model_dump(mode="json")
            return plan

    def render_video(
        self,
        runtime: PipelineRuntime,
        plan: AssetPlan,
        narration: NarrationAsset,
        subtitles: SubtitleAsset,
        content: GeneratedShort,
    ) -> Path:
        with self._stage(runtime, "video_rendering", {"segments": len(plan.segments)}):
            final_video_path = runtime.artifacts.video_dir / f"{safe_slug(content.title)}.mp4"
            output_path = self.ffmpeg.render_short(
                plan=plan,
                audio_path=narration.normalized_path,
                subtitle_path=subtitles.ass_path or subtitles.srt_path,
                working_dir=runtime.artifacts.video_dir,
                output_path=final_video_path,
            )
            runtime.stage_summaries["video_rendering"] = {"output_path": str(output_path)}
            return output_path

    def validate_output(self, runtime: PipelineRuntime, final_video_path: Path) -> dict[str, object]:
        with self._stage(runtime, "validation", {"video_path": str(final_video_path)}):
            payload = self.ffmpeg.validate_video(final_video_path)
            write_json(runtime.artifacts.metadata_dir / "validation.json", payload)
            runtime.stage_summaries["validation"] = payload
            return payload

    def export_to_downloads(
        self,
        runtime: PipelineRuntime,
        final_video_path: Path,
        content: GeneratedShort,
    ) -> Path | None:
        with self._stage(runtime, "downloads_export", {"downloads_enabled": runtime.request.save_to_downloads}):
            if not runtime.request.save_to_downloads:
                runtime.stage_summaries["downloads_export"] = {"copied": False}
                return None
            file_name = f"{safe_slug(content.title)}.mp4"
            copied_path = copy_collision_safe(final_video_path, self.settings.downloads_dir, file_name=file_name)
            runtime.stage_summaries["downloads_export"] = {"copied": True, "path": str(copied_path)}
            return copied_path

    def upload_if_requested(
        self,
        runtime: PipelineRuntime,
        final_video_path: Path,
        content: GeneratedShort,
    ) -> UploadMetadata:
        with self._stage(runtime, "youtube_upload", {"requested": runtime.request.upload}):
            if not runtime.request.upload:
                metadata = UploadMetadata(
                    youtube_video_id=None,
                    privacy_status=runtime.request.privacy_status or self.settings.default_privacy_status,
                    uploaded=False,
                )
                runtime.stage_summaries["youtube_upload"] = metadata.model_dump(mode="json")
                return metadata
            self.youtube.authenticate(force=False)
            metadata = self.youtube.upload_video(
                video_path=final_video_path,
                title=content.title,
                description=f"{content.description}\n\n{' '.join(content.hashtags)}",
                hashtags=content.hashtags,
                privacy_status=runtime.request.privacy_status or self.settings.default_privacy_status,
                response_path=runtime.artifacts.responses_dir / "youtube_upload.json",
            )
            runtime.stage_summaries["youtube_upload"] = metadata.model_dump(mode="json")
            return metadata

    def persist_run(
        self,
        *,
        runtime: PipelineRuntime,
        topic: TopicChoice,
        content: GeneratedShort,
        narration: NarrationAsset,
        subtitles: SubtitleAsset,
        clips: list[VideoClipAsset],
        final_video_path: Path,
        downloads_copy_path: Path | None,
        validation_payload: dict[str, object],
        upload_metadata: UploadMetadata,
        started_at: str,
    ) -> ShortRunResult:
        with self._stage(runtime, "persistence", {"topic": topic.topic, "title": content.title}):
            metadata = {
                "run_id": runtime.run_id,
                "started_at": started_at,
                "completed_at": _utc_now_iso(),
                "topic": topic.model_dump(mode="json"),
                "content": content.model_dump(mode="json"),
                "narration": narration.model_dump(mode="json"),
                "subtitles": subtitles.model_dump(mode="json"),
                "clips": [clip.model_dump(mode="json") for clip in clips],
                "validation": validation_payload,
                "upload": upload_metadata.model_dump(mode="json"),
                "stages": runtime.stage_summaries,
            }
            metadata_path = write_json(runtime.artifacts.metadata_dir / "run_metadata.json", metadata)
            completed_at = metadata["completed_at"]
            self.database.record_topic(
                topic=topic.topic,
                bucket=topic.bucket,
                title=content.title,
                run_id=runtime.run_id,
                created_at=completed_at,
                normalized_topic=normalize_for_similarity(topic.topic),
            )
            self.database.record_asset(
                run_id=runtime.run_id,
                asset_type="narration",
                source_id=None,
                source_url=None,
                local_path=str(narration.normalized_path),
                metadata=narration.model_dump(mode="json"),
                created_at=completed_at,
            )
            self.database.record_asset(
                run_id=runtime.run_id,
                asset_type="subtitles",
                source_id=None,
                source_url=None,
                local_path=str(subtitles.srt_path),
                metadata=subtitles.model_dump(mode="json"),
                created_at=completed_at,
            )
            for clip in clips:
                self.database.record_asset(
                    run_id=runtime.run_id,
                    asset_type="stock_clip",
                    source_id=clip.source_id,
                    source_url=clip.source_url,
                    local_path=str(clip.local_path),
                    metadata=clip.model_dump(mode="json"),
                    created_at=completed_at,
                )
            self.database.record_asset(
                run_id=runtime.run_id,
                asset_type="final_video",
                source_id=None,
                source_url=None,
                local_path=str(final_video_path),
                metadata={"downloads_copy_path": str(downloads_copy_path) if downloads_copy_path else None},
                created_at=completed_at,
            )
            if upload_metadata.uploaded:
                response_payload = {}
                if upload_metadata.response_path and upload_metadata.response_path.exists():
                    response_payload = {"response_path": str(upload_metadata.response_path)}
                self.database.record_upload(
                    run_id=runtime.run_id,
                    youtube_video_id=upload_metadata.youtube_video_id,
                    privacy_status=upload_metadata.privacy_status,
                    response=response_payload,
                    uploaded_at=completed_at,
                )
            self.database.mark_run_success(
                run_id=runtime.run_id,
                bucket=topic.bucket,
                topic=topic.topic,
                title=content.title,
                output_path=str(final_video_path),
                downloads_path=str(downloads_copy_path) if downloads_copy_path else None,
                metadata=metadata,
                completed_at=completed_at,
                duration_seconds=narration.duration_seconds,
                upload_status="uploaded" if upload_metadata.uploaded else "not_uploaded",
            )
            result = ShortRunResult(
                run_id=runtime.run_id,
                title=content.title,
                topic=topic.topic,
                bucket=topic.bucket,
                duration_seconds=narration.duration_seconds,
                output_path=final_video_path,
                downloads_copy_path=downloads_copy_path,
                uploaded=upload_metadata.uploaded,
                youtube_video_id=upload_metadata.youtube_video_id,
                log_path=runtime.logging_bundle.human_log_path,
                metadata_path=metadata_path,
            )
            runtime.stage_summaries["persistence"] = result.model_dump(mode="json")
            return result

    @contextmanager
    def _stage(self, runtime: PipelineRuntime, stage_name: str, input_summary: dict[str, object]):
        self.database.update_run_stage(runtime.run_id, stage_name)
        start = perf_counter()
        runtime.logger.info(
            f"{stage_name}: start",
            extra={"run_id": runtime.run_id, "stage": stage_name, "input_summary": input_summary},
        )
        try:
            yield
        except Exception:
            runtime.logger.exception(
                f"{stage_name}: error",
                extra={"run_id": runtime.run_id, "stage": stage_name},
            )
            raise
        else:
            duration = round(perf_counter() - start, 2)
            runtime.logger.info(
                f"{stage_name}: finish",
                extra={
                    "run_id": runtime.run_id,
                    "stage": stage_name,
                    "duration_seconds": duration,
                    "output_summary": runtime.stage_summaries.get(stage_name, {}),
                },
            )

    def _preferred_topic(self, preferred_topic: str, preferred_bucket: str | None) -> TopicChoice:
        normalized_topic = preferred_topic.strip().lower()
        for bucket, topics in self._topic_catalog_items():
            if normalized_topic in {topic.lower() for topic in topics}:
                chosen_bucket = preferred_bucket.strip().lower() if preferred_bucket else bucket
                return TopicChoice(
                    bucket=chosen_bucket,
                    topic=next(topic for topic in topics if topic.lower() == normalized_topic),
                    visual_queries=[preferred_topic, f"{preferred_topic} cinematic", chosen_bucket],
                    search_terms=[preferred_topic, f"{preferred_topic} {chosen_bucket}", chosen_bucket],
                )
        raise PipelineStageError(
            stage="topic_selection",
            message="Preferred topic is not in the curated catalog.",
            probable_cause="Use a topic from the allowed buckets only.",
        )

    def _fallback_topic_excluding(self, excluded_topics: list[str]) -> TopicChoice:
        excluded = {item.lower() for item in excluded_topics}
        for bucket, topics in self._topic_catalog_items():
            for topic in topics:
                if topic.lower() not in excluded:
                    return TopicChoice(
                        bucket=bucket,
                        topic=topic,
                        visual_queries=[topic, f"{topic} cinematic", bucket],
                        search_terms=[topic, f"{topic} {bucket}", bucket],
                    )
        return self._preferred_topic("axolotls", "animals")

    def _topic_catalog_items(self) -> list[tuple[str, list[str]]]:
        from youtube_kanaal.models.content import TOPIC_CATALOG

        return list(TOPIC_CATALOG.items())

    def _retitle_requested_topic_content(
        self,
        content: GeneratedShort,
        recent_titles: list[str],
    ) -> GeneratedShort | None:
        candidates = [
            f"3 Facts About {content.topic}",
            f"3 Wild Facts About {content.topic}",
            f"3 Surprising Facts About {content.topic}",
            f"3 Quick Facts About {content.topic}",
            f"{content.topic}: 3 Facts You Should Know",
        ]
        payload = content.model_dump(mode="json")
        for candidate in candidates:
            if is_near_duplicate(candidate, recent_titles, self.settings.similarity_threshold):
                continue
            payload["title"] = candidate
            return GeneratedShort.model_validate(payload)
        return None

    def _build_video_queries(self, topic: TopicChoice, content: GeneratedShort) -> list[str]:
        topic_text = topic.topic
        lowered_facts = " ".join(content.facts).lower()
        queries: list[str] = []

        if topic.bucket == "space":
            queries.extend(
                [
                    f"{topic_text} in space",
                    f"{topic_text} ringed planet" if "saturn" in topic_text.lower() else f"{topic_text} planet animation",
                    f"{topic_text} planet animation",
                    "solar system animation",
                ]
            )
        elif topic.bucket == "animals":
            if self._is_marine_topic(topic_text, lowered_facts):
                queries.extend(
                    [
                        f"{topic_text} underwater",
                        f"{topic_text} macro underwater",
                        "reef macro ocean life",
                        "underwater close up marine life",
                    ]
                )
            else:
                queries.extend(
                    [
                        f"{topic_text} close up",
                        f"{topic_text} wildlife",
                        f"{topic_text} nature",
                        "animal close up",
                    ]
                )
        elif topic.bucket == "ocean":
            queries.extend(
                [
                    f"{topic_text} underwater",
                    f"{topic_text} ocean",
                    "underwater macro ocean life",
                    "reef underwater",
                ]
            )
        elif topic.bucket == "geography":
            queries.extend(
                [
                    f"{topic_text} landscape",
                    f"{topic_text} drone",
                    f"{topic_text} nature",
                    "travel landscape drone",
                ]
            )
        elif topic.bucket == "architecture":
            queries.extend(
                [
                    f"{topic_text} architecture",
                    f"{topic_text} drone",
                    f"{topic_text} exterior",
                    "architectural detail",
                ]
            )
        elif topic.bucket == "weather":
            queries.extend(
                [
                    f"{topic_text} storm",
                    f"{topic_text} sky",
                    f"{topic_text} slow motion",
                    "weather timelapse",
                ]
            )
        elif topic.bucket == "food":
            queries.extend(
                [
                    f"{topic_text} close up",
                    f"{topic_text} preparation",
                    f"{topic_text} macro",
                    "food slow motion",
                ]
            )
        elif topic.bucket == "human body":
            queries.extend(
                [
                    f"{topic_text} anatomy animation",
                    f"{topic_text} medical animation",
                    "science animation body",
                ]
            )
        elif topic.bucket == "history":
            queries.extend(
                [
                    f"{topic_text} ruins",
                    f"{topic_text} historical site",
                    f"{topic_text} ancient architecture",
                ]
            )
        elif topic.bucket == "inventions":
            queries.extend(
                [
                    f"{topic_text} invention",
                    f"{topic_text} machine close up",
                    f"{topic_text} technology",
                ]
            )

        queries.extend(self._fact_visual_queries(topic, content))
        queries.extend(topic.visual_queries)
        queries.extend(content.keyword_queries())
        cleaned_queries = []
        for query in queries:
            normalized = " ".join(query.split()).strip()
            if not normalized:
                continue
            if normalized.lower() == topic.bucket.lower():
                continue
            if topic.bucket == "space" and normalized.lower() == topic_text.lower():
                continue
            if normalized not in cleaned_queries:
                cleaned_queries.append(normalized)
        return cleaned_queries[:8]

    def _is_marine_topic(self, topic: str, facts_text: str) -> bool:
        marine_terms = {
            "shrimp",
            "octopus",
            "octopuses",
            "whale",
            "whales",
            "reef",
            "coral",
            "sea",
            "ocean",
            "mangrove",
            "turtle",
            "turtles",
            "kelp",
            "plankton",
        }
        combined = f"{topic} {facts_text}".lower()
        return any(term in combined for term in marine_terms)

    def _fact_visual_queries(self, topic: TopicChoice, content: GeneratedShort) -> list[str]:
        fact_text = " ".join(content.facts).lower()
        topic_text = topic.topic
        queries: list[str] = []

        keyword_map = {
            "ring": f"{topic_text} ringed planet animation",
            "rings": f"{topic_text} rings in space",
            "moon": f"{topic_text} moon in space",
            "moons": f"{topic_text} moon orbit animation",
            "river": f"{topic_text} river aerial",
            "rainforest": f"{topic_text} rainforest river",
            "reef": f"{topic_text} reef underwater",
            "coral": f"{topic_text} coral reef",
            "underwater": f"{topic_text} underwater macro",
            "ocean": f"{topic_text} ocean close up",
            "storm": f"{topic_text} planet storm animation",
            "lightning": f"{topic_text} lightning storm",
            "mountain": f"{topic_text} mountain drone",
            "volcano": f"{topic_text} volcano landscape",
            "castle": f"{topic_text} castle exterior",
            "bridge": f"{topic_text} bridge drone",
            "brain": f"{topic_text} brain animation",
            "heart": f"{topic_text} heart animation",
            "fish": f"{topic_text} fish underwater",
            "bird": f"{topic_text} bird close up",
            "planet": f"{topic_text} in space",
            "space": f"{topic_text} astronomy animation",
        }

        for keyword, query in keyword_map.items():
            if keyword in fact_text and query not in queries:
                queries.append(query)
        return queries[:4]


def validate_artifact_directory(run_id: str, run_dir: Path) -> ValidationResult:
    required_files = [
        run_dir / "audio" / "narration.wav",
        run_dir / "subtitles" / "captions.srt",
        run_dir / "metadata" / "run_metadata.json",
    ]
    final_video_dir = run_dir / "video"
    mp4_files = list(final_video_dir.glob("*.mp4")) if final_video_dir.exists() else []
    checks: list[str] = []
    errors: list[str] = []

    for path in required_files:
        if path.exists():
            checks.append(f"Found {path.name}")
        else:
            errors.append(f"Missing required artifact: {path}")

    if mp4_files:
        checks.append(f"Found final video: {mp4_files[0].name}")
    else:
        errors.append(f"Missing final MP4 in {final_video_dir}")

    return ValidationResult(run_id=run_id, valid=not errors, checks=checks, errors=errors)
