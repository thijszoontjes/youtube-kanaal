from __future__ import annotations

import hashlib
import logging
import math
import shutil
import textwrap
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
    ImageAsset,
    LongRunRequest,
    LongRunResult,
    LongVideoSection,
    NarrationAsset,
    SubtitleAsset,
    TopicChoice,
    UploadMetadata,
)
from youtube_kanaal.pipelines.short_pipeline import RunArtifacts, ShortPipeline, _new_run_id, _utc_now_iso
from youtube_kanaal.services.ffmpeg_service import FFmpegService
from youtube_kanaal.services.illustration_service import IllustrationService
from youtube_kanaal.services.narration_service import NarrationService
from youtube_kanaal.services.ollama_service import OllamaService
from youtube_kanaal.services.pexels_service import PexelsService
from youtube_kanaal.services.thumbnail_service import ThumbnailService
from youtube_kanaal.services.youtube_service import YouTubeService
from youtube_kanaal.utils.files import copy_collision_safe, ensure_directory, safe_slug, write_json, write_text
from youtube_kanaal.utils.subtitles import SubtitleCue, parse_srt_text
from youtube_kanaal.utils.similarity import is_near_duplicate, normalize_for_similarity


LONG_FORM_THEME_ROTATION: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("extreme science", ("space", "ocean", "weather")),
    ("dark history", ("history", "geography", "architecture")),
    ("mysteries and danger", ("technology", "inventions", "vehicles")),
    ("human limits", ("human body", "sports", "food")),
    ("animals and nature", ("animals", "ocean", "geography")),
)

_BRIDGE_SOURCE_NOTES: tuple[dict[str, str], ...] = (
    {
        "title": "6 Popular Types Of Bridges And How They Work",
        "publisher": "Institution of Civil Engineers",
        "url": "https://ice.org.uk/news-views-insights/inside-infrastructure/guide-to-types-of-bridges",
        "supports": "General overview of beam, arch, truss, cantilever, cable-stayed, and suspension forms and their basic load paths.",
        "checked_at": "2026-10-01",
    },
    {
        "title": "Unit 08.04.08: Bridges",
        "publisher": "Yale National Initiative",
        "url": "https://teachers.yale.edu/curriculum/units/2008/4/8",
        "supports": "Educational overview of six bridge forms and how their geometry relates to compression and tension.",
        "checked_at": "2026-10-01",
    },
    {
        "title": "Building Bridges Activity: Beam, Truss, Arch, and Suspension",
        "publisher": "AIA New York Center for Architecture",
        "url": "https://www.centerforarchitecture.org/k-12/resources/building-bridges-activity-beam-truss-arch-and-suspension/",
        "supports": "Diagram-based educational material about four bridge forms and structural forces.",
        "checked_at": "2026-10-01",
    },
)


def _theme_buckets_after(recent_buckets: list[str]) -> list[str]:
    for recent_bucket in recent_buckets:
        for index, (_, buckets) in enumerate(LONG_FORM_THEME_ROTATION):
            if recent_bucket.lower() in {bucket.lower() for bucket in buckets}:
                return list(LONG_FORM_THEME_ROTATION[(index + 1) % len(LONG_FORM_THEME_ROTATION)][1])
    return list(LONG_FORM_THEME_ROTATION[0][1])


def _long_form_theme(bucket: str) -> str:
    for theme, buckets in LONG_FORM_THEME_ROTATION:
        if bucket.lower() in {item.lower() for item in buckets}:
            return theme
    return "general explainer"


def _build_long_audio_ranges(
    cues: list[SubtitleCue],
    word_counts: list[int],
    total_duration: float,
) -> list[tuple[float, float]] | None:
    """Map intro/chapters to the actual subtitle timeline instead of word ratios."""
    if not cues or not word_counts or sum(word_counts) <= 0:
        return None
    cue_word_counts = [max(len(cue.text.split()), 1) for cue in cues]
    cue_total = sum(cue_word_counts)
    # Whisper can omit words on a difficult recording. A badly mismatched cue
    # list would collapse later chapters at the end of the audio timeline, so
    # use the script's section ratios when the subtitle transcript is incomplete.
    if cue_total < max(sum(word_counts) * 0.85, 1):
        return None

    def time_at_word(offset: int) -> float:
        if offset <= 0:
            return 0.0
        consumed = 0
        for cue, cue_words in zip(cues, cue_word_counts):
            if offset <= consumed + cue_words:
                # Never cut a chapter inside an active subtitle cue. Waiting for
                # the cue to finish keeps the spoken explanation and its image
                # together instead of switching while the sentence is still heard.
                return min(max(cue.end_seconds, 0.0), total_duration)
            consumed += cue_words
        return total_duration

    offsets = [0]
    for word_count in word_counts:
        offsets.append(offsets[-1] + max(word_count, 0))
    boundaries = [time_at_word(offset) for offset in offsets]
    boundaries[-1] = total_duration
    ranges = [
        (min(start, end), max(start, end))
        for start, end in zip(boundaries, boundaries[1:])
    ]
    if any(end - start <= 0.001 for start, end in ranges):
        return None
    return ranges


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

    def _preferred_topic_buckets(self, recent_buckets: list[str]) -> list[str]:
        return _theme_buckets_after(self.database.recent_long_buckets(limit=20))

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
            topic = self.select_topic(runtime, long_form=True)  # type: ignore[arg-type]
            runtime.stage_summaries["topic_selection"]["theme"] = _long_form_theme(topic.bucket)
            source_notes = self._verified_source_notes(topic.topic)
            source_status = "reviewed_reference_notes_available" if source_notes else "not_verified_for_topic"
            write_json(
                runtime.artifacts.metadata_dir / "research_sources.json",
                {"status": source_status, "sources": source_notes},
            )
            runtime.stage_summaries["research_sources"] = {"status": source_status, "sources": source_notes}
            speech_rate_wpm = 150.0
            content = self.generate_long_content(runtime, topic, target_speech_rate_wpm=speech_rate_wpm)
            narration = self.generate_long_narration(runtime, content)
            if runtime.request.visual_style == "illustrated_explainer":
                attempts = max(1, self.settings.retry_attempts)
                for attempt in range(1, attempts + 1):
                    target_seconds = runtime.request.test_duration_seconds
                    duration_fits = (
                        target_seconds - 5 <= narration.duration_seconds <= target_seconds + 5
                        if target_seconds is not None
                        else self.settings.min_long_duration_seconds
                        <= narration.duration_seconds
                        <= self.settings.max_long_duration_seconds
                    )
                    if duration_fits:
                        break
                    if attempt == attempts:
                        target_range = (
                            f"{target_seconds}s ±5s"
                            if target_seconds is not None
                            else f"{self.settings.min_long_duration_seconds}-{self.settings.max_long_duration_seconds}s"
                        )
                        raise PipelineStageError(
                            stage="narration_generation",
                            message="Natural TTS narration did not fit the requested video duration after bounded rewrites.",
                            probable_cause=(
                                f"Requested duration {target_range}; final measured narration was "
                                f"{narration.duration_seconds:.2f}s. No speech was trimmed or padded."
                            ),
                        )
                    measured_wpm = len(content.narration.split()) * 60 / max(narration.duration_seconds, 0.1)
                    runtime.logger.info(
                        "Rewriting illustrated script to match measured TTS pace",
                        extra={"attempt": attempt, "measured_wpm": round(measured_wpm, 1)},
                    )
                    content = self.generate_long_content(
                        runtime,
                        topic,
                        target_speech_rate_wpm=measured_wpm,
                        prior_content=content,
                        revision_reason=(
                            f"Measured narration was {narration.duration_seconds:.2f} seconds; "
                            f"the target is {target_seconds if target_seconds is not None else 'the configured long-form range'} seconds."
                        ),
                    )
                    narration = self.generate_long_narration(runtime, content)
            subtitles = self.generate_long_subtitles(runtime, content, narration)
            mixed_audio = self.mix_background_music(runtime, narration)
            clips = self.download_long_broll(runtime, topic, content)
            plan = self.plan_long_assets(runtime, clips, narration, content, subtitles)
            final_video_path = self.render_long_video(runtime, plan, mixed_audio, subtitles, content)
            validation_payload = self.validate_long_output(runtime, final_video_path)
            thumbnail_path = self.generate_thumbnail(runtime, final_video_path)
            upload_metadata = self.upload_long_if_requested(runtime, final_video_path, thumbnail_path, content)
            result = self.persist_long_run(
                runtime=runtime,
                topic=topic,
                content=content,
                narration=narration,
                subtitles=subtitles,
                clips=clips,
                final_video_path=final_video_path,
                thumbnail_path=thumbnail_path,
                validation_payload=validation_payload,
                upload_metadata=upload_metadata,
                started_at=started_at,
            )
            cleanup = self.cleanup_uploaded_media(runtime, upload_metadata=upload_metadata, clips=clips)
            result.media_cleaned = cleanup["cleaned"]
            result.cleanup_deleted_bytes = int(cleanup["deleted_bytes"])
            result.cleanup_summary_path = Path(cleanup["summary_path"]) if cleanup.get("summary_path") else None
            runtime.logger.info(
                "Long-form run completed",
                extra={
                    "run_id": run_id,
                    "output_path": str(final_video_path),
                    "media_cleaned": result.media_cleaned,
                    "cleanup_deleted_bytes": result.cleanup_deleted_bytes,
                },
            )
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

    def generate_long_content(
        self,
        runtime: "LongPipelineRuntime",
        topic: TopicChoice,
        *,
        target_speech_rate_wpm: float = 150.0,
        prior_content: GeneratedLongVideo | None = None,
        revision_reason: str | None = None,
    ) -> GeneratedLongVideo:
        recent_titles = self.database.recent_titles(limit=100)
        with self._long_stage(runtime, "long_content_generation", {"topic": topic.topic, "recent_titles": len(recent_titles)}):
            if runtime.request.script_path is not None:
                supplied = GeneratedLongVideo.model_validate_json(runtime.request.script_path.read_text(encoding="utf-8"))
                chapter_subjects = [section.chapter_subject for section in supplied.sections]
                content = self.ollama.normalize_reviewed_long_content(
                    supplied,
                    topic,
                    target_duration_seconds=runtime.request.test_duration_seconds,
                    visual_style=runtime.request.visual_style,
                    chapter_subjects=chapter_subjects,
                    target_speech_rate_wpm=target_speech_rate_wpm,
                    verified_source_notes=runtime.stage_summaries.get("research_sources", {}).get("sources", []),
                )
                write_text(runtime.artifacts.prompts_dir / "long_content_generation.txt", "Using supplied reviewed script JSON.")
                write_json(runtime.artifacts.responses_dir / "long_content_generation.json", content.model_dump(mode="json"))
                write_text(runtime.artifacts.responses_dir / "long_script.txt", content.narration)
                runtime.stage_summaries["chapter_subject_plan"] = chapter_subjects
                runtime.stage_summaries["long_content_generation"] = {
                    **content.model_dump(mode="json"),
                    "model_id": "reviewed local script",
                    "content_source": str(runtime.request.script_path),
                }
                return content

            chapter_subjects = (
                [section.chapter_subject for section in prior_content.sections]
                if prior_content is not None
                else self.ollama.generate_long_chapter_plan(
                    topic=topic,
                    prompt_path=runtime.artifacts.prompts_dir / "long_chapter_plan.txt",
                    response_path=runtime.artifacts.responses_dir / "long_chapter_plan.json",
                    target_duration_seconds=runtime.request.test_duration_seconds,
                    visual_style=runtime.request.visual_style,
                )
            )
            runtime.stage_summaries["chapter_subject_plan"] = chapter_subjects
            for _ in range(self.settings.retry_attempts):
                content = self.ollama.generate_long_content(
                    topic=topic,
                    excluded_titles=recent_titles,
                    prompt_path=runtime.artifacts.prompts_dir / "long_content_generation.txt",
                    response_path=runtime.artifacts.responses_dir / "long_content_generation.json",
                    target_duration_seconds=runtime.request.test_duration_seconds,
                    chapter_subjects=chapter_subjects,
                    visual_style=runtime.request.visual_style,
                    target_speech_rate_wpm=target_speech_rate_wpm,
                    production_duration_seconds=(
                        (self.settings.min_long_duration_seconds + self.settings.max_long_duration_seconds) // 2
                        if runtime.request.visual_style == "illustrated_explainer"
                        else None
                    ),
                    verified_source_notes=runtime.stage_summaries.get("research_sources", {}).get("sources", []),
                    prior_content=prior_content,
                    revision_reason=revision_reason,
                )
                if prior_content is not None or not is_near_duplicate(
                    content.title,
                    recent_titles,
                    self.settings.similarity_threshold,
                ):
                    runtime.stage_summaries["long_content_generation"] = {
                        **content.model_dump(mode="json"),
                        "model_id": self.settings.ollama_model,
                    }
                    write_text(runtime.artifacts.responses_dir / "long_script.txt", content.narration)
                    return content
                retitled = self._retitle_long_content(content, recent_titles)
                if retitled is not None:
                    runtime.stage_summaries["long_content_generation"] = {
                        **retitled.model_dump(mode="json"),
                        "model_id": self.settings.ollama_model,
                    }
                    write_text(runtime.artifacts.responses_dir / "long_script.txt", retitled.narration)
                    return retitled
                recent_titles.append(content.title)
            raise PipelineStageError(
                stage="long_content_generation",
                message="Generated long-form title was too similar to recent history.",
                probable_cause="Ollama produced near-duplicate titles repeatedly.",
            )

    def generate_long_narration(self, runtime: "LongPipelineRuntime", content: GeneratedLongVideo) -> NarrationAsset:
        with self._long_stage(runtime, "narration_generation", {"topic": content.topic, "words": len(content.narration.split())}):
            raw_extension = ".aiff" if self.settings.long_narration_engine == "macos_say" else ".wav"
            raw_path = runtime.artifacts.audio_dir / f"long_narration_raw{raw_extension}"
            normalized_path = runtime.artifacts.audio_dir / "long_narration_normalized.wav"
            fitted_path = runtime.artifacts.audio_dir / "long_narration.wav"
            synthesis = self.narration.synthesize(
                text=content.narration,
                output_path=raw_path,
                logger=runtime.logger,
                long_form=True,
                engine_override=self.settings.long_narration_engine,
            )
            self.ffmpeg.normalize_audio(input_path=raw_path, output_path=normalized_path)
            current_duration = self.ffmpeg.audio_duration_seconds(normalized_path)
            if runtime.request.visual_style == "illustrated_explainer":
                fitted_path = normalized_path
                fitted_duration = current_duration
            elif runtime.request.test_duration_seconds is not None:
                _, fitted_duration = self.ffmpeg.fit_audio_duration(
                    input_path=normalized_path,
                    output_path=fitted_path,
                    current_duration_seconds=current_duration,
                    min_seconds=runtime.request.test_duration_seconds,
                    max_seconds=runtime.request.test_duration_seconds,
                    target_seconds=runtime.request.test_duration_seconds,
                    preserve_natural_speed=True,
                )
            else:
                _, fitted_duration = self.ffmpeg.fit_audio_duration(
                    input_path=normalized_path,
                    output_path=fitted_path,
                    current_duration_seconds=current_duration,
                    min_seconds=self.settings.min_long_duration_seconds,
                    max_seconds=self.settings.max_long_duration_seconds,
                    preserve_natural_speed=True,
                )
            asset = NarrationAsset(raw_path=raw_path, normalized_path=fitted_path, duration_seconds=fitted_duration)
            voice_id = {
                "piper": self.settings.piper_voice_model_path.name if self.settings.piper_voice_model_path else self.settings.default_piper_voice,
                "kokoro": self.settings.kokoro_voice,
                "chatterbox": self.settings.chatterbox_model,
                "xtts": self.settings.xtts_model_name,
                "macos_say": self.settings.macos_say_voice,
            }.get(synthesis.engine_used)
            runtime.stage_summaries["narration_generation"] = {
                "requested_engine": synthesis.requested_engine,
                "engine": synthesis.engine_used,
                "voice_id": voice_id,
                "voice_parameters": (
                    {
                        "length_scale": self.settings.piper_length_scale,
                        "noise_scale": self.settings.piper_noise_scale,
                        "noise_w_scale": self.settings.piper_noise_w_scale,
                        "sentence_silence_seconds": self.settings.piper_sentence_silence,
                    }
                    if synthesis.engine_used == "piper"
                    else {"kokoro_speed": self.settings.kokoro_speed}
                    if synthesis.engine_used == "kokoro"
                    else {"rate_words_per_minute": self.settings.macos_say_rate}
                    if synthesis.engine_used == "macos_say"
                    else {}
                ),
                "fallback_reason": synthesis.fallback_reason,
                "before_fit_seconds": current_duration,
                "measured_speech_rate_wpm": round(len(content.narration.split()) * 60 / max(current_duration, 0.1), 1),
                "fit_policy": "natural_audio_no_trim_or_padding" if runtime.request.visual_style == "illustrated_explainer" else "legacy_duration_fit",
                **asset.model_dump(mode="json"),
            }
            return asset

    def generate_long_subtitles(
        self,
        runtime: "LongPipelineRuntime",
        content: GeneratedLongVideo,
        narration: NarrationAsset,
    ) -> SubtitleAsset:
        with self._long_stage(runtime, "subtitle_generation", {"audio_seconds": narration.duration_seconds}):
            subtitles = self.whisper.generate_subtitles(
                audio_path=narration.normalized_path,
                subtitle_text=content.narration,
                output_base_path=runtime.artifacts.subtitles_dir / "long_captions",
                duration_seconds=narration.duration_seconds,
                style_profile="long",
            )
            runtime.stage_summaries["subtitle_generation"] = subtitles.model_dump(mode="json")
            return subtitles

    def mix_background_music(self, runtime: "LongPipelineRuntime", narration: NarrationAsset) -> Path:
        with self._long_stage(runtime, "background_music", {"duration_seconds": narration.duration_seconds}):
            mixed_path = runtime.artifacts.audio_dir / "long_voice_music_mix.wav"
            if runtime.request.visual_style == "illustrated_explainer":
                shutil.copy2(narration.normalized_path, mixed_path)
                runtime.stage_summaries["background_music"] = {
                    "path": str(mixed_path),
                    "applied": False,
                    "source": "none; clean narration is the illustrated profile default",
                }
                return mixed_path
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
    ) -> list[ImageAsset]:
        if runtime.request.visual_style == "illustrated_explainer":
            with self._long_stage(
                runtime,
                "illustration_generation",
                {"chapter_count": len(content.sections), "external_images": False},
            ):
                illustration_service = IllustrationService()
                assets: list[ImageAsset] = []
                plans: dict[str, dict[str, object]] = {}
                for index, section in enumerate(content.sections, start=1):
                    try:
                        chapter_assets, plan = illustration_service.create_chapter_scenes(
                            section=section,
                            topic=topic.topic,
                            output_dir=runtime.artifacts.assets_dir / "illustrations",
                            chapter_index=index,
                        )
                    except ValueError as exc:
                        raise PipelineStageError(
                            stage="illustration_generation",
                            message=f"The illustration plan for {section.title!r} is incomplete.",
                            probable_cause=str(exc),
                        ) from exc
                    assets.extend(chapter_assets)
                    plans[f"chapter-{index:02d}"] = plan.model_dump(mode="json")
                runtime.stage_summaries["illustration_generation"] = {
                    "image_count": len(assets),
                    "source": "generated locally by IllustrationService",
                    "rights_status": "generated_local; no external image license asserted",
                    "plans": plans,
                }
                return assets
        queries = self._long_visual_queries(topic, content)
        with self._long_stage(runtime, "stock_image_download", {"query_count": len(queries), "max_images": self.settings.long_broll_clip_count}):
            clips = self.pexels.fetch_broll_photos(
                queries=queries,
                max_photos=self.settings.long_broll_clip_count,
                response_path=runtime.artifacts.responses_dir / "pexels_long_photo_search.json",
            )
            runtime.stage_summaries["stock_image_download"] = {
                "image_count": len(clips),
                "queries": queries,
                "section_queries": {section.title: section.visual_queries for section in content.sections},
            }
            return clips

    def plan_long_assets(
        self,
        runtime: "LongPipelineRuntime",
        clips: list[ImageAsset],
        narration: NarrationAsset,
        content: GeneratedLongVideo,
        subtitles: SubtitleAsset,
    ) -> AssetPlan:
        with self._long_stage(runtime, "asset_planning", {"clip_count": len(clips), "duration": narration.duration_seconds}):
            if runtime.request.visual_style == "illustrated_explainer":
                return self._plan_illustrated_assets(runtime, clips, narration, content, subtitles)
            if not clips:
                raise PipelineStageError(
                    stage="asset_planning",
                    message="Long-form rendering needs at least one photo asset.",
                    probable_cause="Pexels returned no usable photos for the selected topic.",
                )
            overview_path = self._create_long_overview(runtime, clips, content)
            segments: list[AssetPlanSegment] = []
            total_duration = narration.duration_seconds
            section_word_counts = [max(len(section.narration.split()), 1) for section in content.sections]
            intro_word_count = len(content.intro.split())
            subtitle_cues = parse_srt_text(subtitles.srt_path.read_text(encoding="utf-8")) if subtitles.srt_path.exists() else []
            timed_ranges = _build_long_audio_ranges(
                subtitle_cues,
                [intro_word_count, *section_word_counts],
                total_duration,
            )
            if timed_ranges and len(timed_ranges) == len(content.sections) + 1:
                overview_intro = timed_ranges[0][1] if overview_path else 0.0
                chapter_ranges = timed_ranges[1:]
            else:
                overview_intro = min(8.0, max(6.0, total_duration * 0.08)) if overview_path else 0.0
                chapter_audio_duration = max(total_duration - overview_intro, 0.5)
                total_section_words = sum(section_word_counts)
                chapter_durations = [chapter_audio_duration * (count / total_section_words) for count in section_word_counts]
                cursor = overview_intro
                chapter_ranges = []
                for chapter_duration in chapter_durations:
                    chapter_ranges.append((cursor, cursor + chapter_duration))
                    cursor += chapter_duration
            overview_cards = runtime.stage_summaries.get("overview_cards", [])
            card_by_chapter = {
                str(card.get("chapter_id")): card
                for card in overview_cards
                if isinstance(card, dict)
            }
            if overview_path:
                overview_titles = ", ".join(section.title for section in content.sections[:8])
                segments.append(
                    AssetPlanSegment(
                        clip_path=overview_path,
                        duration_seconds=overview_intro,
                        reason=f"OVERVIEW: {overview_titles}",
                        on_screen_text="Everything covered in this video",
                        visual_variant="reveal",
                        scene_id="scene-overview-intro",
                        chapter_id="overview",
                        visual_type="overview",
                        asset_id="overview",
                    )
                )

            for section_index, (section, (chapter_start, chapter_end)) in enumerate(
                zip(content.sections, chapter_ranges),
                start=1,
            ):
                chapter_id = f"chapter-{section_index:02d}"
                chapter_duration = max(chapter_end - chapter_start, 0.0)
                card = card_by_chapter.get(chapter_id, {})
                focus_x = float(card.get("focus_x", 0.5)) if card else 0.5
                focus_y = float(card.get("focus_y", 0.5)) if card else 0.5
                return_duration = 0.0
                if overview_path and section_index > 1:
                    return_duration = min(2.0, max(1.5, chapter_duration * 0.18), max(chapter_duration - 0.75, 0.0))
                transition_duration = (
                    min(0.8, max(0.6, chapter_duration * 0.08), max(chapter_duration - return_duration - 0.5, 0.0))
                    if overview_path
                    else 0.0
                )
                # A very short range must remain inside its chapter. In that
                # case use the chapter photo for the complete spoken block.
                if chapter_duration < 0.5 or return_duration + transition_duration + 0.5 > chapter_duration:
                    return_duration = 0.0
                    transition_duration = 0.0
                if return_duration >= 0.5:
                    segments.append(
                        AssetPlanSegment(
                            clip_path=overview_path,
                            duration_seconds=return_duration,
                            reason=f"RETURN OVERVIEW: {section.title}",
                            on_screen_text=section.title,
                            visual_variant="reveal",
                            scene_id=f"scene-{chapter_id}-overview",
                            chapter_id=chapter_id,
                            narration_fragment=section.title,
                            visual_type="overview",
                            asset_id="overview",
                            focus_x=focus_x,
                            focus_y=focus_y,
                        )
                )
                if transition_duration >= 0.5:
                    section_pool = self._section_clip_pool(section, clips, content.topic, content.bucket)
                    transition_asset = section_pool[1] if len(section_pool) > 1 else section_pool[0]
                    segments.append(
                        AssetPlanSegment(
                            clip_path=transition_asset.local_path,
                            duration_seconds=transition_duration,
                            reason=f"CARD TO CHAPTER: {section.title}",
                            on_screen_text=section.title,
                            visual_variant="reveal",
                            scene_id=f"scene-{chapter_id}-transition",
                            chapter_id=chapter_id,
                            narration_fragment=section.title,
                            visual_type="transition",
                            asset_id=transition_asset.source_id,
                            focus_x=focus_x,
                            focus_y=focus_y,
                        )
                )

                photo_duration = max(chapter_duration - return_duration - transition_duration, 0.0)
                section_pool = self._section_clip_pool(section, clips, content.topic, content.bucket)
                visual_durations = self._visual_cut_durations(photo_duration)
                visual_variants = ("primary", "cutaway", "proof", "punch")
                for visual_index, visual_duration in enumerate(visual_durations, start=1):
                    clip = section_pool[(visual_index - 1) % len(section_pool)]
                    segments.append(
                        AssetPlanSegment(
                            clip_path=clip.local_path,
                            duration_seconds=visual_duration,
                            reason=f"{section.title}: {clip.query}",
                            on_screen_text=section.title,
                            scene_id=f"scene-{chapter_id}-photo-{visual_index:02d}",
                            chapter_id=chapter_id,
                            narration_fragment=section.narration if visual_index == 1 else "",
                            visual_type="photo",
                            visual_variant=visual_variants[(visual_index - 1) % len(visual_variants)],
                            asset_id=clip.source_id,
                            motion=True,
                        )
                    )
            planned_duration = sum(segment.duration_seconds for segment in segments)
            duration_delta = total_duration - planned_duration
            if segments and abs(duration_delta) > 0.001:
                last_segment = segments[-1]
                adjusted_duration = last_segment.duration_seconds + duration_delta
                if adjusted_duration >= 0.5:
                    last_segment.duration_seconds = adjusted_duration
            timeline_cursor = 0.0
            for segment in segments:
                segment.start_seconds = timeline_cursor
                timeline_cursor += segment.duration_seconds
            plan = AssetPlan(segments=segments, total_duration_seconds=narration.duration_seconds)
            runtime.stage_summaries["asset_planning"] = {
                **plan.model_dump(mode="json"),
                "timing_source": "whisper_subtitle_cues" if timed_ranges else "script_word_ratio_estimate",
                "visual_event_count": len(segments),
            }
            return plan

    def _plan_illustrated_assets(
        self,
        runtime: "LongPipelineRuntime",
        assets: list[ImageAsset],
        narration: NarrationAsset,
        content: GeneratedLongVideo,
        subtitles: SubtitleAsset,
    ) -> AssetPlan:
        if not assets:
            raise PipelineStageError(
                stage="asset_planning",
                message="Illustrated rendering needs locally generated diagrams.",
                probable_cause="No valid illustration assets were generated for the selected chapters.",
            )
        assets_by_id = {asset.source_id: asset for asset in assets}
        chapter_word_counts = [max(len(section.narration.split()), 1) for section in content.sections]
        cue_text = subtitles.srt_path.read_text(encoding="utf-8") if subtitles.srt_path.exists() else ""
        cues = parse_srt_text(cue_text)
        timed_ranges = _build_long_audio_ranges(
            cues,
            [len(content.intro.split()), *chapter_word_counts],
            narration.duration_seconds,
        )
        if timed_ranges and len(timed_ranges) == len(content.sections) + 1:
            chapter_ranges = timed_ranges[1:]
            timing_source = "whisper_script_aligned_subtitle_cues"
        else:
            intro_duration = narration.duration_seconds * len(content.intro.split()) / max(
                len(content.narration.split()), 1
            )
            chapter_duration = max(narration.duration_seconds - intro_duration, 0.5)
            total_words = sum(chapter_word_counts)
            chapter_ranges = []
            cursor = intro_duration
            for words in chapter_word_counts:
                duration = chapter_duration * words / max(total_words, 1)
                chapter_ranges.append((cursor, cursor + duration))
                cursor += duration
            timing_source = "script_word_ratio_estimate"

        motion_cycle = ("reveal", "pointer", "slow_push", "pan")
        segments: list[AssetPlanSegment] = []
        frame_total = max(round(narration.duration_seconds * 30), 1)
        snapped_ranges: list[tuple[float, float]] = []
        prior_end_frame = 0
        for index, (start, end) in enumerate(chapter_ranges):
            start_frame = max(prior_end_frame, round(start * 30))
            end_frame = frame_total if index == len(chapter_ranges) - 1 else max(start_frame + 1, round(end * 30))
            snapped_ranges.append((start_frame / 30, end_frame / 30))
            prior_end_frame = end_frame

        first_section = content.sections[0]
        intro_asset = assets_by_id.get("illustration-ch01-structure")
        intro_end = snapped_ranges[0][0] if snapped_ranges else 0.0
        if intro_asset is not None and intro_end >= 0.5:
            segments.append(
                AssetPlanSegment(
                    clip_path=intro_asset.local_path,
                    duration_seconds=intro_end,
                    start_seconds=0,
                    reason=f"INTRO: {first_section.visual_plan.relation}",
                    on_screen_text=content.topic,
                    scene_id="scene-intro-hook",
                    chapter_id="intro",
                    narration_fragment=content.intro,
                    visual_type="diagram",
                    transition="cut",
                    asset_id=intro_asset.source_id,
                    diagram_kind=first_section.visual_plan.kind,
                    diagram_variant=first_section.visual_plan.variant,
                    diagram_phase="structure",
                    diagram_labels=first_section.visual_plan.elements,
                    diagram_relation=first_section.visual_plan.relation,
                    motion_kind="reveal",
                    focus_x=0.5,
                    focus_y=0.55,
                )
            )
        for chapter_index, (section, (start, end)) in enumerate(zip(content.sections, snapped_ranges), start=1):
            chapter_id = f"chapter-{chapter_index:02d}"
            chapter_duration = end - start
            beat_durations = self._frame_aligned_cut_durations(chapter_duration, target_seconds=7.0)
            if not beat_durations:
                raise PipelineStageError(
                    stage="asset_planning",
                    message=f"Chapter {section.title!r} has no usable timed narration range.",
                    probable_cause=f"Computed chapter interval was {start:.2f}-{end:.2f} seconds.",
                )
            chapter_assets = [
                assets_by_id.get(f"illustration-ch{chapter_index:02d}-structure"),
                assets_by_id.get(f"illustration-ch{chapter_index:02d}-load_path"),
            ]
            chapter_assets = [asset for asset in chapter_assets if asset is not None]
            if not chapter_assets:
                raise PipelineStageError(
                    stage="asset_planning",
                    message=f"No diagram assets match chapter {section.title!r}.",
                    probable_cause="The generated asset IDs do not match the chapter planning contract.",
                )
            cursor = start
            for beat_index, beat_duration in enumerate(beat_durations):
                asset = chapter_assets[beat_index % len(chapter_assets)]
                beat_start = cursor
                beat_end = cursor + beat_duration
                fragment = " ".join(
                    cue.text.replace("\n", " ")
                    for cue in cues
                    if cue.end_seconds > beat_start and cue.start_seconds < beat_end
                ).strip()
                segments.append(
                    AssetPlanSegment(
                        clip_path=asset.local_path,
                        duration_seconds=beat_duration,
                        start_seconds=beat_start,
                        reason=f"{section.title}: {section.visual_plan.relation}",
                        on_screen_text=section.title,
                        scene_id=f"scene-{chapter_id}-{beat_index + 1:02d}",
                        chapter_id=chapter_id,
                        narration_fragment=fragment or (section.narration if beat_index == 0 else ""),
                        visual_type="diagram",
                        transition="cut",
                        asset_id=asset.source_id,
                        diagram_kind=section.visual_plan.kind,
                        diagram_variant=section.visual_plan.variant,
                        diagram_phase="structure" if asset.source_id.endswith("structure") else "load_path",
                        diagram_labels=section.visual_plan.elements,
                        diagram_relation=section.visual_plan.relation,
                        motion_kind=motion_cycle[(chapter_index + beat_index - 2) % len(motion_cycle)],
                        focus_x=(0.30, 0.50, 0.70)[beat_index % 3],
                        focus_y=0.58,
                    )
                )
                cursor = beat_end

        if segments:
            duration_error = frame_total / 30 - sum(segment.duration_seconds for segment in segments)
            segments[-1].duration_seconds += duration_error
            timeline_cursor = 0.0
            for segment in segments:
                segment.start_seconds = timeline_cursor
                timeline_cursor += segment.duration_seconds
        planned_duration = sum(segment.duration_seconds for segment in segments)
        target_duration = frame_total / 30
        if abs(planned_duration - target_duration) > 1 / 30:
            raise PipelineStageError(
                stage="asset_planning",
                message="Illustrated scene timeline does not close to the narration duration.",
                probable_cause=f"Timeline is {planned_duration:.3f}s for {target_duration:.3f}s of frame-aligned audio.",
            )
        plan = AssetPlan(segments=segments, total_duration_seconds=target_duration)
        runtime.stage_summaries["asset_planning"] = {
            **plan.model_dump(mode="json"),
            "timing_source": timing_source,
            "chapter_ranges": [
                {"chapter_id": f"chapter-{index:02d}", "start_seconds": start, "end_seconds": end}
                for index, (start, end) in enumerate(chapter_ranges, start=1)
            ],
            "visual_event_count": len(segments),
            "timeline_error_frames": round(abs(planned_duration - target_duration) * 30, 3),
        }
        return plan

    @staticmethod
    def _frame_aligned_cut_durations(duration_seconds: float, target_seconds: float = 6.5) -> list[float]:
        total_frames = round(duration_seconds * 30)
        if total_frames <= 0:
            return []
        count = max(1, math.ceil(total_frames / max(round(target_seconds * 30), 1)))
        base_frames, extra_frames = divmod(total_frames, count)
        return [(base_frames + (1 if index < extra_frames else 0)) / 30 for index in range(count)]

    @staticmethod
    def _visual_cut_durations(duration_seconds: float, target_seconds: float = 6.5) -> list[float]:
        """Split a chapter into human-paced visual beats instead of one static card."""
        if duration_seconds <= 0:
            return []
        count = max(1, math.ceil(duration_seconds / target_seconds))
        base_duration = duration_seconds / count
        durations = [round(base_duration, 2) for _ in range(count)]
        durations[-1] = round(duration_seconds - sum(durations[:-1]), 2)
        return durations

    def _long_frame_size(self, runtime: "LongPipelineRuntime") -> tuple[int, int]:
        if runtime.request.test_duration_seconds is not None:
            return self.settings.long_preview_width, self.settings.long_preview_height
        return self.settings.long_output_width, self.settings.long_output_height

    @staticmethod
    def _section_clip_pool(section: LongVideoSection, clips: list[ImageAsset], topic: str, bucket: str = "") -> list[ImageAsset]:
        """Prefer photos returned for this chapter over generic topic photos."""
        normalize = lambda value: " ".join(value.lower().split())
        section_queries = {normalize(query) for query in section.visual_queries if query.strip()}
        generic_queries = {
            normalize(topic),
            normalize(bucket),
            normalize(f"{topic} {section.title}"),
            normalize(f"{topic} human body"),
            normalize(f"{topic} {section.title} human body"),
            normalize(f"{topic} documentary"),
            normalize(f"{topic} documentary b-roll"),
        }
        specific_queries = section_queries - generic_queries
        specific = [clip for clip in clips if normalize(clip.query) in specific_queries]
        if specific:
            return specific
        matching = [clip for clip in clips if normalize(clip.query) in section_queries]
        return matching or clips

    def _create_long_overview(
        self,
        runtime: "LongPipelineRuntime",
        clips: list[ImageAsset],
        content: GeneratedLongVideo,
    ) -> Path | None:
        """Create the opening photo-zine: one centered card per chapter."""
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageOps

            valid_clips: list[ImageAsset] = []
            for clip in self._select_long_overview_clips(clips, content):
                try:
                    with Image.open(clip.local_path):
                        valid_clips.append(clip)
                except (OSError, ValueError):
                    continue
            if not valid_clips:
                return None

            width, height = self._long_frame_size(runtime)
            canvas = Image.new("RGB", (width, height), "white")
            draw = ImageDraw.Draw(canvas)
            scale = min(width / 1280, height / 720)
            try:
                title_font = ImageFont.truetype("Arial.ttf", max(24, round(28 * scale)))
                label_font = ImageFont.truetype("Arial.ttf", max(16, round(18 * scale)))
            except OSError:
                title_font = ImageFont.load_default()
                label_font = ImageFont.load_default()
            heading = "WHAT THIS VIDEO COVERS"
            heading_box = draw.textbbox((0, 0), heading, font=title_font)
            draw.text(((width - (heading_box[2] - heading_box[0])) / 2, round(14 * scale)), heading, fill="black", font=title_font)
            subheading = f"A CLEAR ROUTE THROUGH {content.topic.upper()}"[:72]
            subheading_box = draw.textbbox((0, 0), subheading, font=label_font)
            draw.text(
                ((width - (subheading_box[2] - subheading_box[0])) / 2, round(50 * scale)),
                subheading,
                fill="#555555",
                font=label_font,
            )

            count = len(valid_clips)
            columns = 1 if count == 1 else 2 if count <= 4 else 3
            rows = (count + columns - 1) // columns
            margin_x = round(44 * scale)
            gap_x = round(24 * scale)
            gap_y = round(20 * scale)
            tile_width = (width - 2 * margin_x - (columns - 1) * gap_x) // columns
            tile_height = round((height - round(120 * scale) - (rows - 1) * gap_y) / rows)
            total_width = columns * tile_width + (columns - 1) * gap_x
            total_height = rows * tile_height + (rows - 1) * gap_y
            start_x = (width - total_width) // 2
            start_y = max(round(62 * scale), (height - total_height) // 2 + round(18 * scale))
            overview_cards: list[dict[str, object]] = []
            for index, clip in enumerate(valid_clips):
                with Image.open(clip.local_path).convert("RGB") as source:
                    tile = ImageOps.fit(source, (tile_width, tile_height), method=Image.Resampling.LANCZOS)
                x = start_x + (index % columns) * (tile_width + gap_x)
                y = start_y + (index // columns) * (tile_height + gap_y)
                canvas.paste(tile, (x, y))
                label = content.sections[index].title if index < len(content.sections) else clip.query
                label = " ".join(label.replace(".", "").split())[:52]
                label_lines = textwrap.wrap(label, width=24)[:2] or [label]
                label_text = "\n".join(label_lines)
                label_box = draw.multiline_textbbox((0, 0), label_text, font=label_font, spacing=1)
                label_height = label_box[3] - label_box[1] + round(10 * scale)
                label_top = y + tile_height - label_height
                draw.rectangle((x, label_top, x + tile_width, y + tile_height), fill="white")
                draw.multiline_text((x + round(8 * scale), label_top + round(4 * scale)), label_text, fill="black", font=label_font, spacing=1)
                chapter_id = f"chapter-{index + 1:02d}"
                overview_cards.append(
                    {
                        "chapter_id": chapter_id,
                        "title": label,
                        "asset_id": clip.source_id,
                        "local_path": str(clip.local_path),
                        "x": x,
                        "y": y,
                        "width": tile_width,
                        "height": tile_height,
                        "focus_x": 0.5,
                        "focus_y": 0.5,
                    }
                )

            output_path = runtime.artifacts.video_dir / "long-overview.jpg"
            canvas.save(output_path, quality=95, subsampling=0)
            runtime.stage_summaries["overview_cards"] = overview_cards
            return output_path
        except (ImportError, OSError, ValueError):
            return None

    def _select_long_overview_clips(
        self,
        clips: list[ImageAsset],
        content: GeneratedLongVideo,
    ) -> list[ImageAsset]:
        """Return one unique visual per chapter; never add loose search results."""
        used_ids: set[str] = set()
        ordered_clips: list[ImageAsset] = []
        for section in content.sections:
            pool = self._section_clip_pool(section, clips, content.topic)
            for clip in pool:
                if clip.source_id not in used_ids:
                    ordered_clips.append(clip)
                    used_ids.add(clip.source_id)
                    break
        return ordered_clips[: len(content.sections)]

    def render_long_video(
        self,
        runtime: "LongPipelineRuntime",
        plan: AssetPlan,
        audio_path: Path,
        subtitles: SubtitleAsset,
        content: GeneratedLongVideo,
    ) -> Path:
        with self._long_stage(runtime, "video_rendering", {"segments": len(plan.segments)}):
            final_video_path = runtime.artifacts.video_dir / f"{safe_slug(content.title)}.mp4"
            output_path = self.ffmpeg.render_longform(
                plan=plan,
                audio_path=audio_path,
                subtitle_path=(subtitles.ass_path or subtitles.srt_path) if runtime.request.burn_captions else None,
                working_dir=runtime.artifacts.video_dir,
                output_path=final_video_path,
                preview_mode=runtime.request.test_duration_seconds is not None,
            )
            runtime.stage_summaries["video_rendering"] = {"output_path": str(output_path), "audio_path": str(audio_path)}
            return output_path

    def validate_long_output(self, runtime: "LongPipelineRuntime", final_video_path: Path) -> dict[str, object]:
        with self._long_stage(runtime, "validation", {"video_path": str(final_video_path)}):
            payload = self.ffmpeg.validate_long_video(
                final_video_path,
                min_seconds=(
                    max(0, runtime.request.test_duration_seconds - 5)
                    if runtime.request.test_duration_seconds is not None
                    else self.settings.min_long_duration_seconds
                ),
                max_seconds=(
                    runtime.request.test_duration_seconds + 5
                    if runtime.request.test_duration_seconds is not None
                    else self.settings.max_long_duration_seconds
                ),
                expected_width=(self.settings.long_preview_width if runtime.request.test_duration_seconds is not None else self.settings.long_output_width),
                expected_height=(self.settings.long_preview_height if runtime.request.test_duration_seconds is not None else self.settings.long_output_height),
            )
            write_json(runtime.artifacts.metadata_dir / "validation.json", payload)
            stream = payload.get("streams", [{}])[0] if isinstance(payload.get("streams"), list) else {}
            report = {
                "video_path": str(final_video_path),
                "checks": {
                    "duration": True,
                    "resolution": {
                        "expected": [
                            self.settings.long_preview_width if runtime.request.test_duration_seconds is not None else self.settings.long_output_width,
                            self.settings.long_preview_height if runtime.request.test_duration_seconds is not None else self.settings.long_output_height,
                        ],
                        "actual": [stream.get("width"), stream.get("height")],
                        "passed": True,
                    },
                    "captions": bool(runtime.stage_summaries.get("subtitle_generation")),
                    "assets_manifest": False,
                },
                "warnings": [],
            }
            write_json(runtime.artifacts.metadata_dir / "validation_report.json", report)
            runtime.stage_summaries["validation"] = payload
            return payload

    def generate_thumbnail(
        self,
        runtime: "LongPipelineRuntime",
        final_video_path: Path,
    ) -> Path:
        with self._long_stage(runtime, "thumbnail_generation", {"source": "first_video_frame"}):
            thumbnail_path = runtime.artifacts.metadata_dir / "thumbnail.jpg"
            self.ffmpeg.extract_frame(
                video_path=final_video_path,
                output_path=thumbnail_path,
                timestamp_seconds=0.0,
            )
            runtime.stage_summaries["thumbnail_generation"] = {
                "path": str(thumbnail_path),
                "size": "first frame",
                "source_video_path": str(final_video_path),
                "timestamp_seconds": 0.0,
            }
            return thumbnail_path

    def upload_long_if_requested(
        self,
        runtime: "LongPipelineRuntime",
        final_video_path: Path,
        thumbnail_path: Path,
        content: GeneratedLongVideo,
    ) -> UploadMetadata:
        upload_status_path = runtime.artifacts.metadata_dir / "upload_status.json"
        with self._long_stage(runtime, "youtube_upload", {"requested": runtime.request.upload, "dry_run": runtime.request.dry_run}):
            chapters = self._chapter_timestamps(
                content,
                runtime.stage_summaries["narration_generation"]["duration_seconds"],
                segments=runtime.stage_summaries.get("asset_planning", {}).get("segments"),
            )
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
        subtitles: SubtitleAsset,
        clips: list[ImageAsset],
        final_video_path: Path,
        thumbnail_path: Path,
        validation_payload: dict[str, object],
        upload_metadata: UploadMetadata,
        started_at: str,
    ) -> LongRunResult:
        with self._long_stage(runtime, "persistence", {"topic": topic.topic, "title": content.title}):
            chapters = self._chapter_timestamps(
                content,
                narration.duration_seconds,
                segments=runtime.stage_summaries.get("asset_planning", {}).get("segments"),
            )
            assets_manifest_path = self._write_assets_manifest(
                runtime=runtime,
                clips=clips,
                narration=narration,
                thumbnail_path=thumbnail_path,
                final_video_path=final_video_path,
            )
            validation_report_path = runtime.artifacts.metadata_dir / "validation_report.json"
            validation_report = {}
            if validation_report_path.exists():
                import json

                validation_report = json.loads(validation_report_path.read_text(encoding="utf-8"))
            validation_report.setdefault("checks", {})["assets_manifest"] = assets_manifest_path.exists()
            validation_report["checks"]["timeline"] = {
                "passed": bool(runtime.stage_summaries.get("asset_planning", {}).get("segments")),
                "scene_count": len(runtime.stage_summaries.get("asset_planning", {}).get("segments", [])),
            }
            write_json(validation_report_path, validation_report)
            metadata_path = runtime.artifacts.metadata_dir / "metadata.json"
            upload_status_path = runtime.artifacts.metadata_dir / "upload_status.json"
            metadata = {
                "run_id": runtime.run_id,
                "started_at": started_at,
                "completed_at": _utc_now_iso(),
                "language": "en",
                "duration_seconds": narration.duration_seconds,
                "visual_style": runtime.request.visual_style,
                "test_duration_seconds": runtime.request.test_duration_seconds,
                "research_sources": runtime.stage_summaries.get("research_sources", {}),
                "subtitles": subtitles.model_dump(mode="json"),
                "topic": topic.model_dump(mode="json"),
                "content": content.model_dump(mode="json"),
                "chapters": [{"start_seconds": seconds, "title": title} for seconds, title in chapters],
                "video_path": str(final_video_path),
                "thumbnail_path": str(thumbnail_path),
                "validation": validation_payload,
                "validation_report_path": str(validation_report_path),
                "assets_manifest_path": str(assets_manifest_path),
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
                content_type="long",
            )
            self.database.record_asset(
                run_id=runtime.run_id,
                asset_type="long_subtitles",
                source_id=None,
                source_url=None,
                local_path=str(subtitles.srt_path),
                metadata=subtitles.model_dump(mode="json"),
                created_at=completed_at,
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
                    asset_type="long_illustration" if runtime.request.visual_style == "illustrated_explainer" else "long_stock_image",
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
                metadata={"size": "1920x1080"},
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

    def _write_assets_manifest(
        self,
        *,
        runtime: "LongPipelineRuntime",
        clips: list[ImageAsset],
        narration: NarrationAsset,
        thumbnail_path: Path,
        final_video_path: Path,
    ) -> Path:
        checked_at = _utc_now_iso()
        assets: list[dict[str, object]] = []

        def add_asset(
            *,
            asset_id: str,
            asset_type: str,
            path: Path,
            rights_status: str,
            source_url: str | None = None,
            license_name: str | None = None,
            license_url: str | None = None,
            edits: list[str] | None = None,
        ) -> None:
            digest = None
            if path.exists():
                hasher = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(chunk)
                digest = hasher.hexdigest()
            assets.append(
                {
                    "asset_id": asset_id,
                    "type": asset_type,
                    "local_path": str(path),
                    "sha256": digest,
                    "source_url": source_url,
                    "license_name": license_name,
                    "license_url": license_url,
                    "rights_status": rights_status,
                    "checked_at": checked_at,
                    "edits": edits or [],
                }
            )

        for clip in clips:
            add_asset(
                asset_id=clip.source_id,
                asset_type="generated_local_illustration" if runtime.request.visual_style == "illustrated_explainer" else "pexels_photo",
                path=clip.local_path,
                source_url=clip.source_url,
                license_name=clip.license_name,
                license_url=clip.license_url,
                rights_status=clip.rights_status,
                edits=(
                    ["original_vector_style_diagram_rendered_as_ppm", "chapter_specific_labels"]
                    if runtime.request.visual_style == "illustrated_explainer"
                    else ["center_crop", "subtle_pan_zoom", "caption_overlay"]
                ),
            )
        add_asset(
            asset_id="first-frame-thumbnail",
            asset_type="frame_extracted_thumbnail",
            path=thumbnail_path,
            rights_status="derived_from_generated_local_video",
            edits=["frame_extracted_at_0_seconds", "converted_to_jpeg"],
        )
        add_asset(asset_id="narration", asset_type="generated_audio", path=narration.normalized_path, rights_status="generated_local_tts")
        raw_narration_path = runtime.artifacts.audio_dir / "long_narration_raw.wav"
        if raw_narration_path.exists():
            add_asset(asset_id="narration-raw", asset_type="generated_audio_source", path=raw_narration_path, rights_status="generated_local_tts")
        for suffix in (".srt", ".vtt", ".ass"):
            subtitle_path = runtime.artifacts.subtitles_dir / f"long_captions{suffix}"
            if subtitle_path.exists():
                add_asset(
                    asset_id=f"captions-{suffix[1:]}",
                    asset_type=f"subtitle_{suffix[1:]}",
                    path=subtitle_path,
                    rights_status="generated_from_audio_transcription",
                )
        music_path = runtime.artifacts.audio_dir / "long_music_bed.wav"
        if music_path.exists():
            add_asset(asset_id="music-bed", asset_type="generated_music_bed", path=music_path, rights_status="generated_procedural")
        script_path = runtime.artifacts.responses_dir / "long_script.txt"
        if script_path.exists():
            add_asset(asset_id="script", asset_type="generated_text", path=script_path, rights_status="generated_local_model")
        add_asset(asset_id="final-video", asset_type="rendered_video", path=final_video_path, rights_status="derived_from_manifested_assets")
        manifest_path = runtime.artifacts.metadata_dir / "assets_manifest.json"
        write_json(manifest_path, {"generated_at": checked_at, "assets": assets})
        runtime.stage_summaries["assets_manifest"] = {"path": str(manifest_path), "asset_count": len(assets)}
        return manifest_path

    def _long_visual_queries(self, topic: TopicChoice, content: GeneratedLongVideo) -> list[str]:
        queries: list[str] = []
        for section in content.sections:
            queries.extend(section.visual_queries)
        queries.extend([topic.topic, f"{topic.topic} {topic.bucket}", f"{topic.topic} documentary"])
        queries.extend(content.keyword_queries())
        return list(dict.fromkeys(" ".join(query.split()).strip() for query in queries if query.strip()))[:16]

    @staticmethod
    def _verified_source_notes(topic: str) -> list[dict[str, str]]:
        normalized = " ".join(topic.casefold().split())
        if normalized in {"bridge", "bridges", "bridge types", "types of bridges"}:
            return [dict(source) for source in _BRIDGE_SOURCE_NOTES]
        return []

    def _chapter_timestamps(
        self,
        content: GeneratedLongVideo,
        duration_seconds: float,
        *,
        segments: object = None,
    ) -> list[tuple[float, str]]:
        if isinstance(segments, list):
            by_chapter: dict[str, float] = {}
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                chapter_id = str(segment.get("chapter_id", ""))
                if not chapter_id.startswith("chapter-"):
                    continue
                start = float(segment.get("start_seconds", 0.0))
                by_chapter[chapter_id] = min(by_chapter.get(chapter_id, start), start)
            if len(by_chapter) == len(content.sections):
                return [
                    (round(by_chapter[f"chapter-{index:02d}"], 3), section.title)
                    for index, section in enumerate(content.sections, start=1)
                ]
        intro_words = len(content.intro.split())
        word_counts = [max(len(section.narration.split()), 1) for section in content.sections]
        total_words = sum(word_counts)
        chapters: list[tuple[float, str]] = []
        total_narration_words = max(intro_words + total_words, 1)
        cursor = duration_seconds * (intro_words / total_narration_words)
        chapter_duration = duration_seconds * (total_words / total_narration_words)
        for index, (section, word_count) in enumerate(zip(content.sections, word_counts)):
            chapters.append((round(cursor, 2), section.title))
            cursor += chapter_duration * (word_count / total_words)
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

    def _retitle_long_content(
        self,
        content: GeneratedLongVideo,
        recent_titles: list[str],
    ) -> GeneratedLongVideo | None:
        topic_title = content.topic[:1].upper() + content.topic[1:]
        candidates = [
            f"The Full Story Behind {topic_title}",
            f"Why {topic_title} Is More Interesting Than It Looks",
            f"What You Should Know About {topic_title}",
            f"The Details That Change How You See {topic_title}",
        ]
        payload = content.model_dump(mode="json")
        for candidate in candidates:
            if is_near_duplicate(candidate, recent_titles, self.settings.similarity_threshold):
                continue
            payload["title"] = candidate
            return GeneratedLongVideo.model_validate(payload)
        return None

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
