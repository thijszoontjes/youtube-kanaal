from __future__ import annotations

import pytest
from pydantic import ValidationError

from youtube_kanaal.config import load_settings
from youtube_kanaal.models.content import GeneratedShort, ShortBeat, TopicChoice
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


def test_generated_short_uses_story_beats_as_narration_source() -> None:
    beats = [
        ShortBeat(beat_type="hook", narration="Octopuses can vanish without leaving the place where they are hiding.", on_screen_text="NOW YOU SEE IT", visual_query="octopus camouflage underwater", energy="high", transition="punch", sfx="impact"),
        ShortBeat(beat_type="setup", narration="Their skin contains tiny organs that can change color in a fraction of a second.", on_screen_text="COLOR INSTANTLY", visual_query="octopus skin color change macro"),
        ShortBeat(beat_type="evidence", narration="Other skin structures bend light and can make the surface look smoother or rougher.", on_screen_text="CHANGES TEXTURE", visual_query="octopus skin texture close up", sfx="tick"),
        ShortBeat(beat_type="escalation", narration="That means the disguise copies both the colors and the physical texture of nearby rocks.", on_screen_text="COPIES THE ROCK", visual_query="octopus mimics rock underwater", energy="high", transition="punch", sfx="riser"),
        ShortBeat(beat_type="payoff", narration="The octopus never became invisible; it made your brain decide that nothing was there.", on_screen_text="YOUR BRAIN MISSED IT", visual_query="camouflaged octopus reveal underwater", energy="high", sfx="silence"),
    ]
    content = GeneratedShort(
        bucket="animals",
        topic="octopuses",
        title="Octopuses Can Hack What Your Eyes See",
        description="A visual explanation of how octopus camouflage changes color, texture, and what the viewer perceives.",
        hashtags=["#Octopus", "#Wildlife", "#Ocean"],
        narration="This deliberately long placeholder narration is replaced by the complete beat narration during model validation and should never survive.",
        facts=[
            "Octopus skin contains organs that rapidly change color.",
            "Octopuses can change the apparent texture of their skin.",
            "Their camouflage can match both the color and texture of nearby rocks.",
        ],
        subtitle_text="This deliberately long placeholder narration is replaced by the complete beat narration during model validation and should never survive.",
        beats=beats,
    )

    assert content.narration == " ".join(beat.narration for beat in beats)
    assert content.subtitle_text == content.narration
    assert [cue["sfx"] for cue in content.beat_sound_cues(24.0)] == ["impact", "none", "tick", "riser", "silence"]
    assert [overlay["beat_type"] for overlay in content.beat_overlays(24.0)] == [
        "hook",
        "setup",
        "evidence",
        "escalation",
        "payoff",
    ]


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
            "That is why Saturn stands out in a fast visual Short made for science fans everywhere. "
            "The scale is huge, but the details are easy to picture."
        ),
        facts=[
            "Saturn has famous rings made mostly of ice.",
            "Saturn has many moons including Titan.",
            "Saturn is so low in density that it would float in water.",
        ],
        subtitle_text=(
            "Here are 3 facts about Saturn. First, Saturn has famous rings made mostly of ice. "
            "Second, Saturn has many moons including Titan. Third, Saturn is so low in density that it would float in water. "
            "That is why Saturn stands out in a fast visual Short made for science fans everywhere. "
            "The scale is huge, but the details are easy to picture."
        ),
    )

    upload_title = short.upload_title()
    upload_description = short.upload_description()
    promoted_description = short.upload_description(include_app_promo=True)

    assert len(short.upload_hashtags()) >= 10
    assert upload_title.count("#") >= 3
    assert "#Saturn" in upload_title
    assert "Download my app SecureSets (Android only):" not in upload_description
    assert "Download my app SecureSets (Android only):" in promoted_description
    assert "https://play.google.com/store/apps/details?id=com.securesets.app&pli=1" in promoted_description
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


def test_ollama_service_normalizes_short_preserves_human_sounding_narration(configured_env) -> None:
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

    assert normalized.title == "SATURN IS HIDING SOMETHING WEIRD"
    assert normalized.title.isupper()
    assert normalized.narration == content.narration
    assert not normalized.narration.startswith("Here are 3 facts about Saturn.")
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
    assert not repaired.narration.lower().startswith("here are 3 facts")
    assert "first," not in repaired.narration.lower()
    assert "that is why" not in repaired.narration.lower()
    assert "people remember" not in repaired.narration.lower()
    assert "looks so unusual on screen" not in repaired.narration.lower()
    assert repaired.subtitle_text == repaired.narration


def test_ollama_service_repairs_generated_short_with_missing_facts_from_narration(configured_env) -> None:
    service = OllamaService(load_settings())

    repaired = service._repair_model_response(
        response_text=(
            '{"bucket":"history","topic":"the Bronze Age",'
            '"title":"The Dawn of Metalworking: 3 Facts About the Bronze Age",'
            '"description":"","hashtags":["#BronzeAge","#AncientHistory","#Metalworking"],'
            '"narration":"The Bronze Age, spanning from around 3000 to 1200 BCE, marked a significant turning point '
            "in human history. It was during this period that humans first discovered how to extract tin and copper "
            "from their ores, leading to the development of bronze, an alloy that provided a substantial increase "
            "in strength over copper. This innovation had far-reaching consequences, enabling early civilizations "
            "like the Egyptians, Mycenaeans, and Sumerians to create more durable tools and weapons. The widespread "
            "adoption of bronze technology also facilitated trade networks across vast distances, helping to "
            'solidify the foundations of complex societies.","facts":[],"subtitle_text":""}'
        ),
        model_cls=GeneratedShort,
    )

    assert repaired is not None
    assert repaired.description.startswith("Three fast facts about the Bronze Age")
    assert len(repaired.facts) == 3
    assert repaired.facts[0].startswith("The Bronze Age")
    assert repaired.subtitle_text == repaired.narration


def test_ollama_service_generate_model_repairs_core_validation_errors(configured_env, tmp_path) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "response": (
                    '{"bucket":"geography","topic":"Patagonia","title":"What Makes Patagonia Unique?",'
                    '"description":"","hashtags":["#PatagoniaGeography"],'
                    '"narration":"Patagonia is a windswept region with dramatic mountains, glaciers, and wildlife.",'
                    '"facts":["The Andes run through Patagonia.","It spans Argentina and Chile.","It includes glaciers and fjords."],'
                    '"subtitle_text":"Patagonia"}'
                )
            }

    service = OllamaService(load_settings())
    service.client = type("FakeClient", (), {"post": lambda self, *args, **kwargs: FakeResponse()})()

    repaired = service._generate_model(
        prompt="irrelevant",
        stage="content_generation",
        prompt_output_path=tmp_path / "content_generation.json",
        model_cls=GeneratedShort,
    )

    assert repaired.description.startswith("Three fast facts about Patagonia")
    assert repaired.topic == "Patagonia"
    assert repaired.bucket == "geography"


def test_ollama_service_normalization_strips_stock_outro_phrases(configured_env) -> None:
    service = OllamaService(load_settings())
    topic = TopicChoice(
        bucket="space",
        topic="Saturn",
        visual_queries=["Saturn", "Saturn rings"],
        search_terms=["Saturn", "space"],
    )
    narration = (
        "Saturn has rings and a moon called Titan. The planet is light for its size, which surprises a lot of people. "
        "Its storms can be dramatic and long lasting in the upper atmosphere, and scientists keep studying them closely. "
        "That mix of scale, motion, and mystery makes Saturn one of the most visually striking planets in short videos. "
        "People remember Saturn because it looks so unusual on screen."
    )
    content = GeneratedShort(
        bucket="space",
        topic="Saturn",
        title="Saturn Ring Wonders",
        description="A short description that is comfortably long enough for validation and metadata.",
        hashtags=["#shorts", "#space", "#saturn"],
        narration=narration,
        facts=[
            "Saturn's rings are made mostly of ice.",
            "Titan is larger than the planet Mercury.",
            "Saturn is so low in density that it would float in water.",
        ],
        subtitle_text=narration,
    )

    normalized = service._normalize_generated_short(content, topic)

    assert "people remember saturn because it looks so unusual on screen" not in normalized.narration.lower()
    assert normalized.narration.endswith("short videos.")
    assert normalized.subtitle_text == normalized.narration


def test_ollama_service_quality_gate_rejects_mismatched_short_title(configured_env) -> None:
    service = OllamaService(load_settings())
    topic = TopicChoice(
        bucket="history",
        topic="the Titanic",
        visual_queries=["shipwreck underwater", "iceberg ocean"],
        search_terms=["the Titanic", "history"],
    )
    content = GeneratedShort(
        bucket="history",
        topic="the Titanic",
        title="DEEP SEA VENTS SHOULD NOT EXIST",
        description="A specific short about the Titanic sinking and the safety lessons that followed.",
        hashtags=["#Titanic", "#History", "#Shipwreck"],
        narration=(
            "The Titanic was not just unlucky; one design limit made the disaster worse. "
            "Its watertight compartments did not reach high enough to stop water spilling between sections. "
            "The ship also carried too few lifeboats for everyone aboard. "
            "After it sank, maritime rules changed so passenger ships had to treat safety very differently. "
            "That makes the disaster feel less random and more preventable."
        ),
        facts=[
            "The Titanic's watertight compartments did not extend high enough to contain flooding.",
            "The Titanic carried too few lifeboats for every passenger and crew member.",
            "The sinking led to major changes in maritime safety rules.",
        ],
        subtitle_text=(
            "The Titanic was not just unlucky; one design limit made the disaster worse. "
            "Its watertight compartments did not reach high enough to stop water spilling between sections. "
            "The ship also carried too few lifeboats for everyone aboard. "
            "After it sank, maritime rules changed so passenger ships had to treat safety very differently. "
            "That makes the disaster feel less random and more preventable."
        ),
    )

    with pytest.raises(ValueError, match="different catalog topic"):
        service._validate_short_quality(content, topic)


def test_ollama_service_quality_gate_rejects_late_topic_mention(configured_env) -> None:
    service = OllamaService(load_settings())
    topic = TopicChoice(
        bucket="history",
        topic="the Titanic",
        visual_queries=["shipwreck underwater", "iceberg ocean"],
        search_terms=["the Titanic", "history"],
    )
    content = GeneratedShort(
        bucket="history",
        topic="the Titanic",
        title="The Titanic Detail Everyone Misses",
        description="A specific short about one Titanic design detail and why it mattered during the sinking.",
        hashtags=["#Titanic", "#History", "#Shipwreck"],
        narration=(
            "Imagine a world where a ship looks impossible to sink. "
            "The ocean seems calm until the design starts working against itself. "
            "The Titanic's watertight compartments did not reach high enough to stop water spilling between sections. "
            "That detail helped turn damage in one area into a disaster across the ship. "
            "The problem was small enough to explain, but big enough to change everything."
        ),
        facts=[
            "The Titanic's watertight compartments did not extend high enough to contain flooding.",
            "The Titanic was marketed as unusually safe before its maiden voyage.",
            "The sinking changed how ships handled lifeboats and radio watch rules.",
        ],
        subtitle_text=(
            "Imagine a world where a ship looks impossible to sink. "
            "The ocean seems calm until the design starts working against itself. "
            "The Titanic's watertight compartments did not reach high enough to stop water spilling between sections. "
            "That detail helped turn damage in one area into a disaster across the ship. "
            "The problem was small enough to explain, but big enough to change everything."
        ),
    )

    with pytest.raises(ValueError, match="first two sentences"):
        service._validate_short_quality(content, topic)
