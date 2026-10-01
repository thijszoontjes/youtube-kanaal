from __future__ import annotations

import logging
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from youtube_kanaal.config import Settings
from youtube_kanaal.models import AssetPlanSegment, GeneratedLongVideo, LongRunRequest, LongVideoSection, NarrationAsset
from youtube_kanaal.pipelines.long_pipeline import LongPipeline, LongPipelineRuntime, _build_long_audio_ranges
from youtube_kanaal.pipelines.short_pipeline import RunArtifacts
from youtube_kanaal.utils.subtitles import SubtitleCue


def _section(index: int) -> LongVideoSection:
    text = " ".join(
        [
            f"Axolotls detail {index} connects the story to clear visual context.",
            "The explanation stays conversational while giving the viewer enough time to follow the idea.",
            "That pacing supports long-form B-roll, simple transitions, and a natural chapter structure.",
        ]
        * 6
    )
    return LongVideoSection(
        chapter_subject=f"Animal {index}",
        title=f"Chapter {index} Detail",
        narration=text,
        visual_queries=["axolotl underwater", "axolotl close up"],
    )


def test_generated_long_video_accepts_required_duration_shape() -> None:
    content = GeneratedLongVideo(
        bucket="animals",
        topic="axolotls",
        title="Axolotls: The Strange Details Most People Miss",
        thumbnail_text="WEIRD SURVIVOR",
        intro="What is really happening inside an axolotl? This video follows the clues chapter by chapter.",
        description=(
            "A long visual explainer about axolotls with chapters, stock footage, narration, and upload metadata. "
            "The package is built for an English channel format with clear pacing and mobile-readable thumbnail text."
        ),
        tags=["axolotls", "animals", "science", "wildlife", "facts", "education", "biology", "explainer"],
        sections=[_section(index) for index in range(1, 8)],
        facts=[
            "Axolotls can be explained through several distinct visual details.",
            "Axolotls support a longer chapter-based story.",
            "Axolotls work well with underwater B-roll.",
            "Axolotls have enough context for a long explainer.",
            "Axolotls can be packaged with searchable metadata.",
            "Axolotls fit the existing channel theme.",
        ],
    )

    assert 510 <= content.estimated_duration_seconds() <= 660
    assert len(content.sections) == 7


def test_generated_long_video_rejects_duplicate_chapter_subjects() -> None:
    sections = [_section(index) for index in range(1, 8)]
    sections[0].chapter_subject = "blue whale"
    sections[1].chapter_subject = "blue whale"

    with pytest.raises(ValueError, match="distinct chapter subjects"):
        GeneratedLongVideo(
            bucket="ocean",
            topic="largest creatures",
            title="The Largest Creatures in the Ocean Explained Clearly",
            thumbnail_text="OCEAN GIANTS",
            intro="Which animals truly dominate the ocean? The answer is stranger than most people expect.",
            description="A chapter-based ocean explainer about the largest animals, with distinct species, visual evidence, and clear narration for every chapter.",
            tags=["ocean", "animals", "wildlife", "science", "facts", "nature", "education", "explainer"],
            sections=sections,
            facts=[
                "The ocean contains several different giant animals.",
                "Each species uses a different survival strategy.",
                "Large animals can occupy very different habitats.",
                "Size does not always predict feeding behavior.",
                "Visual comparisons make scale easier to understand.",
                "A species-by-species structure keeps the story clear.",
            ],
        )


@pytest.mark.parametrize("chapter_count", [7, 8, 9])
def test_generated_long_video_accepts_seven_to_nine_chapters(chapter_count: int) -> None:
    content = GeneratedLongVideo(
        bucket="animals",
        topic="axolotls",
        title="Axolotls: The Strange Details Most People Miss",
        thumbnail_text="WEIRD SURVIVOR",
        intro="What is really happening inside an axolotl? This video follows the clues chapter by chapter.",
        description="A chapter-based axolotl explainer with concrete biology, visual evidence, and a clear narrative arc. Each section connects one visible trait to the mechanism that makes it possible.",
        tags=["axolotls", "animals", "science", "wildlife", "facts", "education", "biology", "explainer"],
        sections=[_section(index) for index in range(1, chapter_count + 1)],
        facts=[
            "Axolotls can be explained through several distinct visual details.",
            "Axolotls support a longer chapter-based story.",
            "Axolotls work well with underwater B-roll.",
            "Axolotls have enough context for a long explainer.",
            "Axolotls can be packaged with searchable metadata.",
            "Axolotls fit the existing channel theme.",
        ],
    )

    assert len(content.sections) == chapter_count


def test_long_run_request_dry_run_disables_upload() -> None:
    request = LongRunRequest(upload=True, dry_run=True)

    assert request.upload is False


def test_illustrated_test_request_keeps_duration_and_visual_profile() -> None:
    request = LongRunRequest(test_duration_seconds=90, visual_style="illustrated_explainer")

    assert request.test_duration_seconds == 90
    assert request.visual_style == "illustrated_explainer"


def test_illustrated_scene_splits_close_on_the_30_fps_frame_grid() -> None:
    durations = LongPipeline._frame_aligned_cut_durations(13.37, target_seconds=7)

    assert sum(durations) == round(13.37 * 30) / 30
    assert all(round(duration * 30) == duration * 30 for duration in durations)
    assert len(durations) == 2


def test_publication_chapters_use_the_rendered_scene_timeline() -> None:
    content = GeneratedLongVideo(
        bucket="animals",
        topic="axolotls",
        title="Axolotls: The Strange Details Most People Miss",
        thumbnail_text="WEIRD SURVIVOR",
        intro="What is really happening inside an axolotl? This video follows the clues chapter by chapter.",
        description=(
            "A long visual explainer about axolotls with chapters, stock footage, narration, and upload metadata. "
            "The package is built for an English channel format with clear pacing and mobile-readable thumbnail text."
        ),
        tags=["axolotls", "animals", "science", "wildlife", "facts", "education", "biology", "explainer"],
        sections=[_section(index) for index in range(1, 8)],
        facts=[
            "Axolotls can be explained through several distinct visual details.",
            "Axolotls support a longer chapter-based story.",
            "Axolotls work well with underwater B-roll.",
            "Axolotls have enough context for a long explainer.",
            "Axolotls can be packaged with searchable metadata.",
            "Axolotls fit the existing channel theme.",
        ],
    )
    segments = [
        {"chapter_id": f"chapter-{index:02d}", "start_seconds": 4.25 + index * 11.5}
        for index in range(1, 8)
    ]

    chapters = LongPipeline._chapter_timestamps(LongPipeline.__new__(LongPipeline), content, 90, segments=segments)

    assert chapters[0] == (15.75, content.sections[0].title)
    assert chapters[-1] == (84.75, content.sections[-1].title)


def test_illustrated_narration_is_not_trimmed_or_padded(tmp_path: Path) -> None:
    class FakeDatabase:
        def update_run_stage(self, *_args, **_kwargs) -> None:
            pass

    class FakeNarration:
        def synthesize(self, *, output_path: Path, **_kwargs):
            output_path.write_bytes(b"spoken-audio")
            return SimpleNamespace(requested_engine="piper", engine_used="piper", fallback_reason=None)

    class FakeFFmpeg:
        def normalize_audio(self, *, input_path: Path, output_path: Path) -> Path:
            shutil.copy2(input_path, output_path)
            return output_path

        def audio_duration_seconds(self, _path: Path) -> float:
            return 89.5

        def fit_audio_duration(self, **_kwargs):
            pytest.fail("The illustrated profile must keep natural narration duration.")

    settings = Settings(output_dir=tmp_path / "output", logs_dir=tmp_path / "logs", mock_mode=False)
    request = LongRunRequest(test_duration_seconds=90, visual_style="illustrated_explainer")
    artifacts = RunArtifacts.create(settings, "natural-audio")
    runtime = LongPipelineRuntime(
        settings=settings,
        request=request,
        run_id="natural-audio",
        artifacts=artifacts,
        logging_bundle=SimpleNamespace(human_log_path=tmp_path / "run.log"),
        logger=logging.getLogger("test-long-natural-audio"),
    )
    pipeline = LongPipeline(
        settings,
        FakeDatabase(),
        narration_service=FakeNarration(),
        ffmpeg_service=FakeFFmpeg(),
    )
    content = GeneratedLongVideo(
        bucket="animals",
        topic="axolotls",
        title="Axolotls: The Strange Details Most People Miss",
        thumbnail_text="WEIRD SURVIVOR",
        intro="What is really happening inside an axolotl?",
        description="An explainer about axolotls, their traits, habitats, and biology with clear spoken explanations and chapter sections, concise examples, and an evidence-based conclusion.",
        tags=["axolotls", "animals", "science", "wildlife", "facts", "education", "biology", "explainer"],
        sections=[_section(index) for index in range(1, 8)],
        facts=[f"Axolotl fact {index} describes a distinct biological detail." for index in range(1, 7)],
    )

    result = pipeline.generate_long_narration(runtime, content)

    assert result.duration_seconds == 89.5
    assert result.normalized_path.name == "long_narration_normalized.wav"
    assert runtime.stage_summaries["narration_generation"]["fit_policy"] == "natural_audio_no_trim_or_padding"


def test_long_asset_plan_segment_keeps_scene_chapter_and_asset_identity() -> None:
    segment = AssetPlanSegment(
        clip_path=Path("chapter-photo.jpg"),
        duration_seconds=6.5,
        reason="Chapter 01: matching visual",
        scene_id="scene-chapter-01-00",
        chapter_id="chapter-01",
        narration_fragment="The first concrete detail.",
        visual_type="photo",
        asset_id="pexels-123",
        motion=False,
    )

    assert segment.scene_id == "scene-chapter-01-00"
    assert segment.chapter_id == "chapter-01"
    assert segment.asset_id == "pexels-123"
    assert segment.narration_fragment.startswith("The first")


def test_long_asset_plan_segment_can_be_static() -> None:
    segment = AssetPlanSegment(
        clip_path=Path("diagram.jpg"),
        duration_seconds=4.0,
        reason="Static evidence card",
        motion=False,
    )

    assert segment.motion is False


def test_long_audio_ranges_follow_real_subtitle_timing() -> None:
    cues = [
        SubtitleCue(start_seconds=0.0, end_seconds=2.0, text="intro words"),
        SubtitleCue(start_seconds=2.0, end_seconds=5.0, text="chapter words"),
        SubtitleCue(start_seconds=5.0, end_seconds=9.0, text="more chapter"),
    ]

    ranges = _build_long_audio_ranges(cues, [2, 4], 9.0)

    assert ranges == [(0.0, 2.0), (2.0, 9.0)]


def test_long_audio_ranges_wait_for_active_subtitle_before_switching() -> None:
    cues = [
        SubtitleCue(start_seconds=0.0, end_seconds=4.0, text="intro words still speaking"),
        SubtitleCue(start_seconds=4.0, end_seconds=7.0, text="chapter words"),
    ]

    ranges = _build_long_audio_ranges(cues, [2, 2], 7.0)

    assert ranges == [(0.0, 4.0), (4.0, 7.0)]


def test_long_audio_ranges_reject_incomplete_whisper_timeline() -> None:
    cues = [
        SubtitleCue(start_seconds=0.0, end_seconds=2.0, text="intro"),
        SubtitleCue(start_seconds=2.0, end_seconds=5.0, text="chapter"),
    ]

    ranges = _build_long_audio_ranges(cues, [10, 10], 10.0)

    assert ranges is None


def test_test_video_validation_allows_five_second_duration_tolerance(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeDatabase:
        def update_run_stage(self, *_args, **_kwargs) -> None:
            pass

    class FakeFFmpeg:
        def validate_long_video(self, _path: Path, **kwargs):
            captured.update(kwargs)
            return {"streams": [{"width": 1280, "height": 720, "duration": "93.43"}], "format": {"duration": "93.43"}}

    settings = Settings(output_dir=tmp_path / "output", logs_dir=tmp_path / "logs")
    runtime = LongPipelineRuntime(
        settings=settings,
        request=LongRunRequest(test_duration_seconds=90, visual_style="illustrated_explainer"),
        run_id="duration-tolerance",
        artifacts=RunArtifacts.create(settings, "duration-tolerance"),
        logging_bundle=SimpleNamespace(human_log_path=tmp_path / "run.log"),
        logger=logging.getLogger("test-long-duration-tolerance"),
    )
    pipeline = LongPipeline(settings, FakeDatabase(), ffmpeg_service=FakeFFmpeg())

    pipeline.validate_long_output(runtime, tmp_path / "rendered.mp4")

    assert captured["min_seconds"] == 85
    assert captured["max_seconds"] == 95
