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
