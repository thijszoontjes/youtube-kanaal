from __future__ import annotations

from youtube_kanaal.utils.similarity import is_near_duplicate, similarity_score


def test_similarity_detects_near_duplicate_title() -> None:
    assert is_near_duplicate(
        "3 Facts About Axolotls",
        ["3 facts about axolotls"],
        0.86,
    )


def test_similarity_score_drops_for_unrelated_text() -> None:
    assert similarity_score("axolotls", "roman aqueducts") < 0.5
