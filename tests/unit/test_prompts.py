from __future__ import annotations

from youtube_kanaal.models.content import TOPIC_SELECTION_CATALOG, TopicChoice, _load_topic_catalog_extensions
from youtube_kanaal.prompts import (
    build_content_generation_prompt,
    build_long_chapter_plan_prompt,
    build_long_content_generation_prompt,
    build_topic_selection_prompt,
)


def test_topic_prompt_contains_catalog_and_exclusions() -> None:
    prompt = build_topic_selection_prompt(["axolotls", "Saturn"])
    assert "Choose exactly one topic from this curated catalog" in prompt
    assert "axolotls" in prompt
    assert "Saturn" in prompt
    assert "the Library of Alexandria" not in prompt
    assert "globally recognizable" in prompt


def test_topic_prompt_can_prefer_long_form_buckets() -> None:
    prompt = build_topic_selection_prompt([], ["space", "ocean", "weather"])

    assert "For this long-form rotation" in prompt
    assert "space, ocean, weather" in prompt


def test_automatic_topic_catalog_prefers_familiar_subjects() -> None:
    selected_topics = {topic for topics in TOPIC_SELECTION_CATALOG.values() for topic in topics}

    assert "the Library of Alexandria" not in selected_topics
    assert "the Titanic" in selected_topics


def test_topic_catalog_extensions_are_loaded_from_json(tmp_path, monkeypatch) -> None:
    extra_path = tmp_path / "topics.json"
    extra_path.write_text('{"history": ["the Eiffel Tower", "the Eiffel Tower"]}', encoding="utf-8")
    monkeypatch.setenv("YOUTUBE_TOPIC_EXTRA_PATH", str(extra_path))

    extensions = _load_topic_catalog_extensions()

    assert extensions == {"history": ["the Eiffel Tower"]}


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
    assert "Generate at least 10 relevant hashtags" in prompt
    assert "natural spoken English" in prompt
    assert '"title_hook": "<clickbait-curiosity alternative title>"' in prompt
    assert "Use full ALL CAPS for some titles" in prompt
    assert "Do NOT Ignore This About Axolotls" in prompt
    assert '"hook_text"' not in prompt
    assert "Every beat needs concrete on_screen_text" in prompt
    assert '"beat_type": "hook"' in prompt
    assert "spoken hook must be 5-12 words" in prompt
    assert "at most 24 characters" in prompt
    assert "end immediately after the strongest idea" in prompt
    assert "Story shape for this Short" in prompt
    assert "exactly four beats: hook, evidence, reversal, payoff" in prompt
    assert "5-7 beats" not in prompt
    assert 'Never use "Here are", "First", "Second", "Third"' in prompt
    assert "open a precise information gap" in prompt
    assert "name or unmistakably identify axolotls" in prompt
    assert 'Do not open with "Did you know", "Imagine a world"' in prompt
    assert "The title must match axolotls" in prompt
    assert 'Do not end with phrases like "That is why..." or "People remember..."' in prompt
    assert "must open with" not in prompt


def test_illustrated_bridge_test_prompt_uses_six_named_bridge_forms() -> None:
    topic = TopicChoice(
        bucket="architecture",
        topic="bridges",
        visual_queries=["bridges", "bridge load paths"],
        search_terms=["bridge types"],
    )

    prompt = build_long_chapter_plan_prompt(topic, 90, visual_style="illustrated_explainer")

    for name in ("Beam Bridge", "Arch Bridge", "Truss Bridge", "Suspension Bridge", "Cable-Stayed Bridge", "Cantilever Bridge"):
        assert name in prompt
    assert "not general chapters about materials" in prompt


def test_illustrated_test_prompt_calculates_duration_word_budget() -> None:
    topic = TopicChoice(
        bucket="architecture",
        topic="bridges",
        visual_queries=["bridges", "bridge load paths"],
        search_terms=["bridge types"],
    )

    prompt = build_long_content_generation_prompt(
        topic,
        [],
        target_duration_seconds=90,
        chapter_subjects=["Beam Bridge", "Arch Bridge", "Truss Bridge", "Suspension Bridge", "Cable-Stayed Bridge", "Cantilever Bridge"],
        visual_style="illustrated_explainer",
        target_speech_rate_wpm=150,
    )

    assert "target 225 total" in prompt
    assert "acceptable total of 185-265 words" in prompt
    assert "Write 32-41 words in EACH" in prompt
    assert "do not pad or repeat" in prompt
