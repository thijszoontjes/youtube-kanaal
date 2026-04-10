from __future__ import annotations

import pytest
from pydantic import ValidationError

from youtube_kanaal.config import load_settings
from youtube_kanaal.models.content import GeneratedShort, TopicChoice
from youtube_kanaal.services.ollama_service import OllamaService


def test_topic_choice_requires_catalog_topic() -> None:
    with pytest.raises(ValidationError):
        TopicChoice(
            bucket="animals",
            topic="dragons",
            visual_queries=["dragons", "dragons flying"],
            search_terms=["dragons"],
        )


def test_topic_choice_accepts_new_gaming_catalog_topic() -> None:
    topic = TopicChoice(
        bucket="gaming",
        topic="Fortnite",
        visual_queries=["Fortnite gaming setup", "Fortnite esports"],
        search_terms=["Fortnite", "gaming"],
    )

    assert topic.bucket == "gaming"
    assert topic.topic == "Fortnite"
    assert topic.search_terms[0] == "Fortnite"


def test_generated_short_requires_three_distinct_facts() -> None:
    with pytest.raises(ValidationError):
        GeneratedShort(
            bucket="animals",
            topic="axolotls",
            title="3 Facts About Axolotls",
            description="A short description that is comfortably long enough for validation.",
            hashtags=["#shorts", "#facts", "#axolotls"],
            narration=(
                "Here are three facts about axolotls. Fact one is interesting and short. "
                "Fact two is also short and interesting. Fact three rounds things out with a final note."
            ),
            facts=["Same fact", "Same fact", "Same fact"],
            subtitle_text="Three facts about axolotls with concise subtitles.",
        )


def test_generated_short_estimated_duration_is_positive() -> None:
    short = GeneratedShort(
        bucket="animals",
        topic="axolotls",
        title="3 Facts About Axolotls",
        description="A short description that is comfortably long enough for validation.",
        hashtags=["#shorts", "#facts", "#axolotls"],
        narration=(
            "Here are three facts about axolotls. Fact one explains their unusual biology in a fast, clear way. "
            "Fact two highlights why scientists keep studying them for regeneration. "
            "Fact three shows why they look so different from most amphibians in short videos. "
            "That unusual combination of science and appearance is exactly why axolotls work so well in a quick Short."
        ),
        facts=[
            "Axolotls can regenerate parts of their bodies.",
            "They keep juvenile traits into adulthood.",
            "They are native to lakes near Mexico City.",
        ],
        subtitle_text="Here are three facts about axolotls in a short, clear narration for subtitles.",
    )

    assert short.estimated_duration_seconds() > 0
    assert len(short.upload_hashtags()) >= 10


def test_generated_short_builds_upload_metadata_with_hashtags() -> None:
    short = GeneratedShort(
        bucket="space",
        topic="Saturn",
        title="3 Facts About Saturn",
        description="A short description that is comfortably long enough for validation and upload metadata.",
        hashtags=["#space", "#saturn", "#planetfacts"],
        narration=(
            "Here are 3 facts about Saturn. First, Saturn has famous rings made mostly of ice. "
            "Second, Saturn has many moons including Titan. Third, Saturn is so low in density that it would float in water. "
            "That is why Saturn stands out in a fast visual Short made for science fans everywhere."
        ),
        facts=[
            "Saturn has famous rings made mostly of ice.",
            "Saturn has many moons including Titan.",
            "Saturn is so low in density that it would float in water.",
        ],
        subtitle_text=(
            "Here are 3 facts about Saturn. First, Saturn has famous rings made mostly of ice. "
            "Second, Saturn has many moons including Titan. Third, Saturn is so low in density that it would float in water. "
            "That is why Saturn stands out in a fast visual Short made for science fans everywhere."
        ),
    )

    upload_title = short.upload_title()
    upload_description = short.upload_description()

    assert len(short.upload_hashtags()) >= 10
    assert upload_title.count("#") >= 3
    assert "#Saturn" in upload_title
    assert "#Space" in upload_description


def test_ollama_service_repairs_bucket_from_catalog_topic(configured_env) -> None:
    service = OllamaService(load_settings())

    repaired = service._repair_model_response(
        response_text=(
            '{"bucket":"youtube shorts","topic":"saturn",'
            '"visual_queries":["Saturn rings","Saturn moons"],'
            '"search_terms":["Saturn","Ring system"]}'
        ),
        model_cls=TopicChoice,
    )

    assert repaired is not None
    assert repaired.bucket == "space"
    assert repaired.topic == "Saturn"


def test_ollama_service_repairs_bucket_only_response(configured_env) -> None:
    service = OllamaService(load_settings())

    repaired = service._repair_model_response(
        response_text=(
            '{"bucket":"youtube","topic":"space",'
            '"visual_queries":["saturn rings"],'
            '"search_terms":["galaxy","universe"]}'
        ),
        model_cls=TopicChoice,
    )

    assert repaired is not None
    assert repaired.bucket == "space"
    assert repaired.topic == "Saturn"


def test_ollama_service_normalizes_short_into_three_facts_intro(configured_env) -> None:
    service = OllamaService(load_settings())
    topic = TopicChoice(
        bucket="space",
        topic="Saturn",
        visual_queries=["Saturn", "Saturn rings"],
        search_terms=["Saturn", "space"],
    )
    content = GeneratedShort(
        bucket="space",
        topic="Saturn",
        title="Saturn Ring Wonders",
        description="A short description that is comfortably long enough for validation and metadata.",
        hashtags=["#shorts", "#space", "#saturn"],
        narration=(
            "Saturn has rings and a moon called Titan. The planet is light for its size, which surprises a lot of people. "
            "Its storms can be dramatic and long lasting in the upper atmosphere, and scientists keep studying them closely. "
            "That mix of scale, motion, and mystery makes Saturn one of the most visually striking planets in short videos."
        ),
        facts=[
            "Saturn's rings are made mostly of ice.",
            "Titan is larger than the planet Mercury.",
            "Saturn is so low in density that it would float in water.",
        ],
        subtitle_text="Something else entirely",
    )

    normalized = service._normalize_generated_short(content, topic)

    assert normalized.title == "3 Facts About Saturn"
    assert normalized.narration.startswith("Here are 3 facts about Saturn.")
    assert "First," in normalized.narration
    assert "Second," in normalized.narration
    assert "Third," in normalized.narration
    assert normalized.subtitle_text == normalized.narration
    assert len(normalized.hashtags) >= 10


def test_ollama_service_repairs_generated_short_with_missing_description(configured_env) -> None:
    service = OllamaService(load_settings())

    repaired = service._repair_model_response(
        response_text=(
            '{"bucket":"animals","topic":"mantis shrimp","title":"Mantis Shrimp Wonders",'
            '"description":"","hashtags":["#oceanlife"],'
            '"narration":"Here are 3 facts about mantis shrimp. Fact 1: They punch fast. Fact 2: They see many colors. '
            'Fact 3: They live in warm seas.","facts":["They punch fast.","They see many colors.","They live in warm seas."],'
            '"subtitle_text":"Mantis shrimp"}'
        ),
        model_cls=GeneratedShort,
    )

    assert repaired is not None
    assert repaired.description.startswith("Three fast facts about mantis shrimp")
    assert len(repaired.hashtags) >= 10
