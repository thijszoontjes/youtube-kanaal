from __future__ import annotations

from pathlib import Path

from youtube_kanaal.models import AssetPlanSegment, GeneratedLongVideo, LongRunRequest, LongVideoSection


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
    )

    assert segment.scene_id == "scene-chapter-01-00"
    assert segment.chapter_id == "chapter-01"
    assert segment.asset_id == "pexels-123"
    assert segment.narration_fragment.startswith("The first")
