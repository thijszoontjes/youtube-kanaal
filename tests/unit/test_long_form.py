from __future__ import annotations

from pathlib import Path

import pytest

from youtube_kanaal.models import AssetPlanSegment, GeneratedLongVideo, LongRunRequest, LongVideoSection
from youtube_kanaal.pipelines.long_pipeline import _build_long_audio_ranges
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
