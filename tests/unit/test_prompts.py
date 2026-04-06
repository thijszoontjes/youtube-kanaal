from __future__ import annotations

from youtube_kanaal.models.content import TopicChoice
from youtube_kanaal.prompts import build_content_generation_prompt, build_topic_selection_prompt


def test_topic_prompt_contains_catalog_and_exclusions() -> None:
    prompt = build_topic_selection_prompt(["axolotls", "Saturn"])
    assert "Choose exactly one topic from this curated catalog" in prompt
    assert "axolotls" in prompt
    assert "Saturn" in prompt


def test_content_prompt_contains_recent_titles() -> None:
    topic = TopicChoice(
        bucket="animals",
        topic="axolotls",
        visual_queries=["axolotls", "axolotls close up"],
        search_terms=["axolotls", "animals"],
    )
    prompt = build_content_generation_prompt(topic, ["3 Facts About Penguins"])
    assert "3 Facts About Penguins" in prompt
    assert '"topic": "axolotls"' in prompt
