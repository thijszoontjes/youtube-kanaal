from __future__ import annotations

from pathlib import Path

import pytest

from youtube_kanaal.models import LongVideoSection
from youtube_kanaal.services.illustration_service import IllustrationService


def _bridge_section(variant: str = "beam") -> LongVideoSection:
    return LongVideoSection(
        chapter_subject=f"{variant.title()} bridge",
        title=f"{variant.title()} bridge",
        narration="A bridge deck transfers traffic loads through its structure and supports into the ground.",
        visual_queries=["bridge deck", "bridge support"],
        visual_plan={
            "kind": "bridge",
            "variant": variant,
            "elements": ["deck", "supports", "ground"],
            "relation": "The deck passes its load through the supports into the ground.",
        },
    )


def test_bridge_illustration_generates_two_original_labeled_ppm_scenes(tmp_path: Path) -> None:
    assets, plan = IllustrationService().create_chapter_scenes(
        section=_bridge_section(),
        topic="bridges",
        output_dir=tmp_path,
        chapter_index=1,
    )

    assert len(assets) == 2
    assert [asset.source_id for asset in assets] == [
        "illustration-ch01-structure",
        "illustration-ch01-load_path",
    ]
    assert all(asset.source_url is None and asset.download_url is None for asset in assets)
    assert all(asset.rights_status == "generated_local" for asset in assets)
    assert all(asset.local_path.read_bytes().startswith(b"P6\n1280 720\n255\n") for asset in assets)
    assert assets[0].local_path.read_bytes() != assets[1].local_path.read_bytes()
    assert plan.variant == "beam"


def test_unsupported_topic_without_a_complete_visual_plan_fails_explicitly(tmp_path: Path) -> None:
    section = LongVideoSection(
        chapter_subject="feedback loop",
        title="Feedback loop",
        narration="A change in one component creates an effect elsewhere in the system.",
        visual_queries=["feedback loop", "system mechanism"],
    )

    with pytest.raises(ValueError, match="no generic fallback"):
        IllustrationService().create_chapter_scenes(
            section=section,
            topic="systems",
            output_dir=tmp_path,
            chapter_index=1,
        )
