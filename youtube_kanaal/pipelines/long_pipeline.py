from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from youtube_kanaal.config import Settings
from youtube_kanaal.db import Database
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.logging_config import LoggingBundle, configure_run_logging
from youtube_kanaal.models import (
    AssetPlan,
    AssetPlanSegment,
    GeneratedLongVideo,
    LongRunRequest,
    LongRunResult,
    NarrationAsset,
    TopicChoice,
    UploadMetadata,
    VideoClipAsset,
)
from youtube_kanaal.pipelines.short_pipeline import RunArtifacts, ShortPipeline, _new_run_id, _utc_now_iso
from youtube_kanaal.services.ffmpeg_service import FFmpegService
from youtube_kanaal.services.narration_service import NarrationService
from youtube_kanaal.services.ollama_service import OllamaService
from youtube_kanaal.services.pexels_service import PexelsService
from youtube_kanaal.services.thumbnail_service import ThumbnailService
from youtube_kanaal.services.youtube_service import YouTubeService
from youtube_kanaal.utils.files import copy_collision_safe, ensure_directory, safe_slug, write_json, write_text
from youtube_kanaal.utils.similarity import is_near_duplicate, normalize_for_similarity


class LongPipeline(ShortPipeline):
    """Long-form video generation pipeline built on the same services as Shorts."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        *,
        ollama_service: OllamaService | None = None,
        narration_service: NarrationService | None = None,
        pexels_service: PexelsService | None = None,
        ffmpeg_service: FFmpegService | None = None,
        youtube_service: YouTubeService | None = None,
        thumbnail_service: ThumbnailService | None = None,
    ) -> None:
        super().__init__(
            settings,
            database,
            ollama_service=ollama_service,
            narration_service=narration_service,
            pexels_service=pexels_service,
            ffmpeg_service=ffmpeg_service,
            youtube_service=youtube_service,
        )
        self.thumbnail = thumbnail_service or ThumbnailService(settings)

    def run(self, request: LongRunRequest) -> LongRunResult:
        run_id = _new_run_id()
        artifacts = RunArtifacts.create(self.settings, run_id)
        logging_bundle = configure_run_logging(run_id, self.settings.logs_dir, debug=request.debug or self.settings.app_debug)
        runtime = LongPipelineRuntime(
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
        runtime.logger.info("Long-form run started", extra={"run_id": run_id, "started_at": started_at})

        try:
            topic = self.select_topic(runtime)  # type: ignore[arg-type]
            content = self.generate_long_content(runtime, topic)
            narration = self.generate_long_narration(runtime, content)
            mixed_audio = self.mix_background_music(runtime, narration)
            clips = self.download_long_broll(runtime, topic, content)
            plan = self.plan_long_assets(runtime, clips, narration, content)
            final_video_path = self.render_long_video(runtime, plan, mixed_audio, content)
            validation_payload = self.validate_long_output(runtime, final_video_path)
            thumbnail_path = self.generate_thumbnail(runtime, clips, content)
            upload_metadata = self.upload_long_if_requested(runtime, final_video_path, thumbnail_path, content)
            result = self.persist_long_run(
                runtime=runtime,
                topic=topic,
                content=content,
                narration=narration,
                clips=clips,
                final_video_path=final_video_path,
                thumbnail_path=thumbnail_path,
                validation_payload=validation_payload,
                upload_metadata=upload_metadata,
                started_at=started_at,
            )
            runtime.logger.info("Long-form run completed", extra={"run_id": run_id, "output_path": str(final_video_path)})
            return result
        except Exception as exc:
            stage_name = exc.stage if isinstance(exc, PipelineStageError) else "pipeline"
            completed_at = _utc_now_iso()
            runtime.logger.exception("Long-form run failed", extra={"run_id": run_id, "stage": stage_name, "error": str(exc)})
            self.database.mark_run_failed(
                run_id=run_id,
                stage=stage_name,
                error_message=str(exc),
                completed_at=completed_at,
            )
            raise

    def generate_long_content(self, runtime: "LongPipelineRuntime", topic: TopicChoice) -> GeneratedLongVideo:
        recent_titles = self.database.recent_titles(limit=100)
        with self._long_stage(runtime, "long_content_generation", {"topic": topic.topic, "recent_titles": len(recent_titles)}):
            for _ in range(self.settings.retry_attempts):
                content = self.ollama.generate_long_content(
                    topic=topic,
                    excluded_titles=recent_titles,
                    prompt_path=runtime.artifacts.prompts_dir / "long_content_generation.txt",
                    response_path=runtime.artifacts.responses_dir / "long_content_generation.json",
                )
                if not is_near_duplicate(content.title, recent_titles, self.settings.similarity_threshold):
                    runtime.stage_summaries["long_content_generation"] = content.model_dump(mode="json")
                    return content
                recent_titles.append(content.title)
            raise PipelineStageError(
                stage="long_content_generation",
                message="Generated long-form title was too similar to recent history.",
                probable_cause="Ollama produced near-duplicate titles repeatedly.",
            )

    def generate_long_narration(self, runtime: "LongPipelineRuntime", content: GeneratedLongVideo) -> NarrationAsset:
        with self._long_stage(runtime, "narration_generation", {"topic": content.topic, "words": len(content.narration.split())}):
            raw_path = runtime.artifacts.audio_dir / "long_narration_raw.wav"
            normalized_path = runtime.artifacts.audio_dir / "long_narration_normalized.wav"
            fitted_path = runtime.artifacts.audio_dir / "long_narration.wav"
            synthesis = self.narration.synthesize(text=content.narration, output_path=raw_path, logger=runtime.logger)
            self.ffmpeg.normalize_audio(input_path=raw_path, output_path=normalized_path)
            current_duration = self.ffmpeg.audio_duration_seconds(normalized_path)
            _, fitted_duration = self.ffmpeg.fit_audio_duration(
                input_path=normalized_path,
                output_path=fitted_path,
                current_duration_seconds=current_duration,
                min_seconds=self.settings.min_long_duration_seconds,
                max_seconds=self.settings.max_long_duration_seconds,
            )
            asset = NarrationAsset(raw_path=raw_path, normalized_path=fitted_path, duration_seconds=fitted_duration)
            runtime.stage_summaries["narration_generation"] = {
                "requested_engine": synthesis.requested_engine,
                "engine": synthesis.engine_used,
                "fallback_reason": synthesis.fallback_reason,
                "before_fit_seconds": current_duration,
                **asset.model_dump(mode="json"),
            }
            return asset

    def mix_background_music(self, runtime: "LongPipelineRuntime", narration: NarrationAsset) -> Path:
        with self._long_stage(runtime, "background_music", {"duration_seconds": narration.duration_seconds}):
            mixed_path = runtime.artifacts.audio_dir / "long_voice_music_mix.wav"
            output_path = self.ffmpeg.mix_longform_audio(
                narration_path=narration.normalized_path,
                duration_seconds=narration.duration_seconds,
                output_path=mixed_path,
            )
            runtime.stage_summaries["background_music"] = {
                "path": str(output_path),
                "ducking": True,
                "source": "procedural royalty-free FFmpeg pink-noise bed",
            }
            return output_path

    def download_long_broll(
        self,
        runtime: "LongPipelineRuntime",
        topic: TopicChoice,
        content: GeneratedLongVideo,
    ) -> list[VideoClipAsset]:
        queries = self._long_visual_queries(topic, content)
        with self._long_stage(runtime, "stock_video_download", {"query_count": len(queries), "max_clips": self.settings.long_broll_clip_count}):
            clips = self.pexels.fetch_broll_clips(
                queries=queries,
                max_clips=self.settings.long_broll_clip_count,
                response_path=runtime.artifacts.responses_dir / "pexels_long_search.json",
            )
            runtime.stage_summaries["stock_video_download"] = {"clip_count": len(clips), "queries": queries}
            return clips

    def plan_long_assets(
        self,
        runtime: "LongPipelineRuntime",
        clips: list[VideoClipAsset],
        narration: NarrationAsset,
        content: GeneratedLongVideo,
    ) -> AssetPlan:
        with self._long_stage(runtime, "asset_planning", {"clip_count": len(clips), "duration": narration.duration_seconds}):
            clip_count = max(len(clips), 1)
            segment_duration = narration.duration_seconds / clip_count
            segment_duration = max(self.settings.long_segment_min_seconds, min(segment_duration, self.settings.long_segment_max_seconds))
            segments: list[AssetPlanSegment] = []
            remaining = narration.duration_seconds
            index = 0
            while remaining > 0.25:
                clip = clips[index % len(clips)]
                duration = min(segment_duration, remaining)
                segments.append(
                    AssetPlanSegment(
                        clip_path=clip.local_path,
                        duration_seconds=max(0.5, duration),
                        reason=f"Long-form B-roll for {content.topic}: {clip.query}",
                    )
                )
                remaining -= duration
                index += 1
            plan = AssetPlan(segments=segments, total_duration_seconds=narration.duration_seconds)
            runtime.stage_summaries["asset_planning"] = plan.model_dump(mode="json")
            return plan

    def render_long_video(
        self,
        runtime: "LongPipelineRuntime",
        plan: AssetPlan,
        audio_path: Path,
        content: GeneratedLongVideo,
    ) -> Path:
        with self._long_stage(runtime, "video_rendering", {"segments": len(plan.segments)}):
            final_video_path = runtime.artifacts.video_dir / f"{safe_slug(content.title)}.mp4"
            output_path = self.ffmpeg.render_longform(
                plan=plan,
                audio_path=audio_path,
                working_dir=runtime.artifacts.video_dir,
                output_path=final_video_path,
            )
            runtime.stage_summaries["video_rendering"] = {"output_path": str(output_path), "audio_path": str(audio_path)}
            return output_path

    def validate_long_output(self, runtime: "LongPipelineRuntime", final_video_path: Path) -> dict[str, object]:
        with self._long_stage(runtime, "validation", {"video_path": str(final_video_path)}):
            payload = self.ffmpeg.validate_long_video(
                final_video_path,
                min_seconds=self.settings.min_long_duration_seconds,
                max_seconds=self.settings.max_long_duration_seconds,
            )
            write_json(runtime.artifacts.metadata_dir / "validation.json", payload)
            runtime.stage_summaries["validation"] = payload
            return payload

    def generate_thumbnail(
        self,
        runtime: "LongPipelineRuntime",
        clips: list[VideoClipAsset],
        content: GeneratedLongVideo,
    ) -> Path:
        with self._long_stage(runtime, "thumbnail_generation", {"topic": content.topic}):
            background_path = runtime.artifacts.assets_dir / "thumbnail_background.jpg"
            if clips:
                self.ffmpeg.extract_frame(video_path=clips[0].local_path, output_path=background_path)
            thumbnail_path = runtime.artifacts.metadata_dir / "thumbnail.jpg"
            output_path = self.thumbnail.generate(
                title_text=content.thumbnail_text,
                topic=content.topic,
                background_path=background_path,
                output_path=thumbnail_path,
            )
            runtime.stage_summaries["thumbnail_generation"] = {"path": str(output_path), "size": "1280x720"}
            return output_path

    def upload_long_if_requested(
        self,
        runtime: "LongPipelineRuntime",
        final_video_path: Path,
        thumbnail_path: Path,
        content: GeneratedLongVideo,
    ) -> UploadMetadata:
        upload_status_path = runtime.artifacts.metadata_dir / "upload_status.json"
        with self._long_stage(runtime, "youtube_upload", {"requested": runtime.request.upload, "dry_run": runtime.request.dry_run}):
            chapters = self._chapter_timestamps(content, runtime.stage_summaries["narration_generation"]["duration_seconds"])
            if not runtime.request.upload:
                metadata = UploadMetadata(
                    youtube_video_id=None,
                    privacy_status=runtime.request.privacy_status or self.settings.default_privacy_status,
                    scheduled_publish_at=runtime.request.scheduled_publish_at,
                    uploaded=False,
                )
                write_json(
                    upload_status_path,
                    {
                        "uploaded": False,
                        "ready_to_upload": True,
                        "reason": "dry-run or upload disabled",
                        "video_path": str(final_video_path),
                        "thumbnail_path": str(thumbnail_path),
                        "metadata_path": str(runtime.artifacts.metadata_dir / "metadata.json"),
                    },
                )
                runtime.stage_summaries["youtube_upload"] = metadata.model_dump(mode="json")
                return metadata
            try:
                self.youtube.authenticate(force=False)
                metadata = self.youtube.upload_video(
                    video_path=final_video_path,
                    title=content.title,
                    description=content.upload_description(chapters),
                    hashtags=content.tags,
                    privacy_status=runtime.request.privacy_status or "private",
                    scheduled_publish_at=runtime.request.scheduled_publish_at,
                    response_path=runtime.artifacts.responses_dir / "youtube_upload.json",
                )
                if metadata.youtube_video_id:
                    self.youtube.upload_thumbnail(
                        video_id=metadata.youtube_video_id,
                        thumbnail_path=thumbnail_path,
                        response_path=runtime.artifacts.responses_dir / "youtube_thumbnail.json",
                    )
                write_json(upload_status_path, metadata.model_dump(mode="json"))
                runtime.stage_summaries["youtube_upload"] = metadata.model_dump(mode="json")
                return metadata
            except PipelineStageError as exc:
                fallback_payload = {
                    "uploaded": False,
                    "ready_to_upload": True,
                    "reason": str(exc),
                    "video_path": str(final_video_path),
                    "thumbnail_path": str(thumbnail_path),
                    "metadata_path": str(runtime.artifacts.metadata_dir / "metadata.json"),
                }
                write_json(upload_status_path, fallback_payload)
                metadata = UploadMetadata(
                    youtube_video_id=None,
                    privacy_status=runtime.request.privacy_status or "private",
                    scheduled_publish_at=runtime.request.scheduled_publish_at,
                    response_path=upload_status_path,
                    uploaded=False,
                )
                runtime.stage_summaries["youtube_upload"] = fallback_payload
                return metadata

    def persist_long_run(
        self,
        *,
        runtime: "LongPipelineRuntime",
        topic: TopicChoice,
        content: GeneratedLongVideo,
        narration: NarrationAsset,
        clips: list[VideoClipAsset],
        final_video_path: Path,
        thumbnail_path: Path,
        validation_payload: dict[str, object],
        upload_metadata: UploadMetadata,
        started_at: str,
    ) -> LongRunResult:
        with self._long_stage(runtime, "persistence", {"topic": topic.topic, "title": content.title}):
            chapters = self._chapter_timestamps(content, narration.duration_seconds)
            metadata_path = runtime.artifacts.metadata_dir / "metadata.json"
            upload_status_path = runtime.artifacts.metadata_dir / "upload_status.json"
            metadata = {
                "run_id": runtime.run_id,
                "started_at": started_at,
                "completed_at": _utc_now_iso(),
                "language": "en",
                "duration_seconds": narration.duration_seconds,
                "topic": topic.model_dump(mode="json"),
                "content": content.model_dump(mode="json"),
                "chapters": [{"start_seconds": seconds, "title": title} for seconds, title in chapters],
                "video_path": str(final_video_path),
                "thumbnail_path": str(thumbnail_path),
                "validation": validation_payload,
                "upload": upload_metadata.model_dump(mode="json"),
                "stages": runtime.stage_summaries,
            }
            write_json(metadata_path, metadata)
            write_text(
                runtime.artifacts.metadata_dir / "metadata.txt",
                self._metadata_text(content, chapters, final_video_path, thumbnail_path),
            )
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
                asset_type="long_narration",
                source_id=None,
                source_url=None,
                local_path=str(narration.normalized_path),
                metadata=narration.model_dump(mode="json"),
                created_at=completed_at,
            )
            for clip in clips:
                self.database.record_asset(
                    run_id=runtime.run_id,
                    asset_type="long_stock_clip",
                    source_id=clip.source_id,
                    source_url=clip.source_url,
                    local_path=str(clip.local_path),
                    metadata=clip.model_dump(mode="json"),
                    created_at=completed_at,
                )
            self.database.record_asset(
                run_id=runtime.run_id,
                asset_type="thumbnail",
                source_id=None,
                source_url=None,
                local_path=str(thumbnail_path),
                metadata={"size": "1280x720"},
                created_at=completed_at,
            )
            self.database.record_asset(
                run_id=runtime.run_id,
                asset_type="long_final_video",
                source_id=None,
                source_url=None,
                local_path=str(final_video_path),
                metadata={"thumbnail_path": str(thumbnail_path)},
                created_at=completed_at,
            )
            if runtime.request.save_to_downloads:
                copy_collision_safe(final_video_path, self.settings.downloads_dir, file_name=final_video_path.name)
                copy_collision_safe(thumbnail_path, self.settings.downloads_dir, file_name=thumbnail_path.name)
            if upload_metadata.uploaded:
                self.database.record_upload(
                    run_id=runtime.run_id,
                    youtube_video_id=upload_metadata.youtube_video_id,
                    privacy_status=upload_metadata.privacy_status,
                    response={"response_path": str(upload_metadata.response_path) if upload_metadata.response_path else None},
                    uploaded_at=completed_at,
                )
            self.database.mark_run_success(
                run_id=runtime.run_id,
                bucket=topic.bucket,
                topic=topic.topic,
                title=content.title,
                output_path=str(final_video_path),
                downloads_path=None,
                metadata=metadata,
                completed_at=completed_at,
                duration_seconds=narration.duration_seconds,
                upload_status="uploaded" if upload_metadata.uploaded else "ready_to_upload",
            )
            result = LongRunResult(
                run_id=runtime.run_id,
                title=content.title,
                topic=topic.topic,
                bucket=topic.bucket,
                duration_seconds=narration.duration_seconds,
                output_path=final_video_path,
                thumbnail_path=thumbnail_path,
                metadata_path=metadata_path,
                upload_status_path=upload_status_path,
                uploaded=upload_metadata.uploaded,
                youtube_video_id=upload_metadata.youtube_video_id,
                privacy_status=upload_metadata.privacy_status,
                scheduled_publish_at=upload_metadata.scheduled_publish_at,
                log_path=runtime.logging_bundle.human_log_path,
            )
            runtime.stage_summaries["persistence"] = result.model_dump(mode="json")
            return result

    def _long_visual_queries(self, topic: TopicChoice, content: GeneratedLongVideo) -> list[str]:
        queries: list[str] = [topic.topic, f"{topic.topic} {topic.bucket}", f"{topic.topic} documentary"]
        for section in content.sections:
            queries.extend(section.visual_queries)
        queries.extend(content.keyword_queries())
        return list(dict.fromkeys(" ".join(query.split()).strip() for query in queries if query.strip()))[:16]

    def _chapter_timestamps(self, content: GeneratedLongVideo, duration_seconds: float) -> list[tuple[float, str]]:
        word_counts = [max(len(section.narration.split()), 1) for section in content.sections]
        total_words = sum(word_counts)
        chapters: list[tuple[float, str]] = []
        cursor = 0.0
        for index, (section, word_count) in enumerate(zip(content.sections, word_counts)):
            chapters.append((round(cursor, 2), section.title))
            cursor += duration_seconds * (word_count / total_words)
            if index == 0 and chapters[0][0] != 0:
                chapters[0] = (0.0, section.title)
        return chapters

    def _metadata_text(
        self,
        content: GeneratedLongVideo,
        chapters: list[tuple[float, str]],
        video_path: Path,
        thumbnail_path: Path,
    ) -> str:
        lines = [
            f"Title: {content.title}",
            "",
            "Description:",
            content.upload_description(chapters),
            "",
            f"Tags: {', '.join(content.tags)}",
            f"Video: {video_path}",
            f"Thumbnail: {thumbnail_path}",
        ]
        return "\n".join(lines).strip() + "\n"

    @contextmanager
    def _long_stage(self, runtime: "LongPipelineRuntime", stage_name: str, input_summary: dict[str, object]):
        self.database.update_run_stage(runtime.run_id, stage_name)
        start = perf_counter()
        runtime.logger.info(
            f"{stage_name}: start",
            extra={"run_id": runtime.run_id, "stage": stage_name, "input_summary": input_summary},
        )
        try:
            yield
        except Exception:
            runtime.logger.exception(f"{stage_name}: error", extra={"run_id": runtime.run_id, "stage": stage_name})
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


class LongPipelineRuntime:
    def __init__(
        self,
        *,
        settings: Settings,
        request: LongRunRequest,
        run_id: str,
        artifacts: RunArtifacts,
        logging_bundle: LoggingBundle,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.request = request
        self.run_id = run_id
        self.artifacts = artifacts
        self.logging_bundle = logging_bundle
        self.logger = logger
        self.stage_summaries: dict[str, dict[str, object]] = {}
