from __future__ import annotations

import json

import httpx
import pytest

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.models import GeneratedLongVideo, GeneratedShort, TopicChoice
from youtube_kanaal.services.ollama_service import OllamaService


def test_visual_query_object_is_flattened_for_pexels() -> None:
    service = OllamaService(Settings(mock_mode=True))

    query = service._coerce_visual_query(
        {
            "subject": "International Space Station",
            "action": "orbiting Earth",
            "scale": "wide shot",
        }
    )

    assert query == "International Space Station orbiting Earth wide shot"


def test_missing_overlay_is_derived_from_existing_narration() -> None:
    service = OllamaService(Settings(mock_mode=True))

    assert service._normalize_overlay("", "The Sun hides violent magnetic storms") == "The Sun hides violent"
    assert service._normalize_overlay("Too many words for this overlay", "unused narration") == "Too many words for"


def test_generation_keeps_ollama_model_loaded_for_followup_requests(tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {
                "response": (
                    '{"bucket":"space","topic":"Saturn",'
                    '"visual_queries":["Saturn rings"],"search_terms":["Saturn"]}'
                )
            }

    class FakeClient:
        def post(self, path: str, **kwargs: object) -> FakeResponse:
            captured.update(kwargs["json"])
            return FakeResponse()

    service = OllamaService(Settings(_env_file=None))
    service.client = FakeClient()
    service.choose_topic(
        excluded_topics=[],
        prompt_path=tmp_path / "topic.txt",
        response_path=tmp_path / "topic.json",
    )

    assert captured["keep_alive"] == "15m"
    assert captured["options"]["num_ctx"] == 4096
    assert captured["options"]["temperature"] == 0.2


def test_generation_uses_catalog_constrained_schema_for_topic_selection() -> None:
    service = OllamaService(Settings(_env_file=None))

    schema = service._response_schema(TopicChoice)

    assert schema["properties"]["bucket"]["enum"]
    assert schema["properties"]["topic"]["enum"]


def test_long_form_uses_json_mode_and_larger_context() -> None:
    service = OllamaService(Settings(_env_file=None))

    assert service._response_schema(GeneratedLongVideo) == "json"
    assert service.settings.ollama_long_context_length == 8192
    assert service.settings.ollama_long_max_output_tokens == 4608


def test_long_form_generation_requests_enough_output_tokens(tmp_path) -> None:
    topic = TopicChoice(
        bucket="inventions",
        topic="GPS",
        visual_queries=["GPS", "GPS satellite"],
        search_terms=["GPS"],
    )
    content = OllamaService(Settings(mock_mode=True))._fallback_long_content(topic)
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"response": content.model_dump_json()}

    class FakeClient:
        def post(self, path: str, **kwargs: object) -> FakeResponse:
            captured.update(kwargs["json"])
            return FakeResponse()

    service = OllamaService(Settings(_env_file=None))
    service.client = FakeClient()
    service._generate_model(
        prompt="irrelevant",
        stage="long_content_generation",
        prompt_output_path=tmp_path / "long_content_generation.json",
        model_cls=GeneratedLongVideo,
    )

    assert captured["options"]["num_predict"] == 4608


def test_generated_long_sections_are_not_padded_with_stock_reason_text() -> None:
    service = OllamaService(Settings(mock_mode=True))

    sections = service._fit_long_sections(
        [
            {
                "title": "GPS satellites",
                "narration": "GPS satellites transmit timing signals that let a receiver calculate distance.",
                "visual_queries": ["GPS satellite", "satellite orbit"],
            }
        ],
        "GPS",
        "inventions",
        pad_short_sections=False,
    )

    assert "The reason this block matters" not in sections[0].narration


def test_short_quality_retry_includes_the_previous_gate_failure(tmp_path, monkeypatch) -> None:
    service = OllamaService(Settings(_env_file=None, retry_attempts=2))
    topic = TopicChoice(
        bucket="space",
        topic="the Sun",
        visual_queries=["the Sun", "solar flare"],
        search_terms=["the Sun", "solar flare"],
    )
    narration = (
        "The Sun looks calm, but its bright surface hides violent magnetic storms above it. "
        "Its visible surface is about 5,500 degrees Celsius, hot enough to glow across space. "
        "Sunspots turn darker because intense magnetic fields block heat from rising through the surface. "
        "Solar flares can disrupt technology on Earth, exposing the Sun's hidden violence without warning."
    )
    content = GeneratedShort(
        bucket="space",
        topic="the Sun",
        title="THE SUN HIDES A VIOLENT SECRET",
        title_hook="Why Sunspots Look Dark",
        description="A visual explanation of the Sun's surface heat, sunspots, and disruptive solar flares.",
        hashtags=["#Sun", "#Space", "#SolarScience"],
        narration=narration,
        facts=[
            "The Sun's visible surface is about 5,500 degrees Celsius.",
            "Sunspots turn darker because intense magnetic fields block heat from rising.",
            "Solar flares can disrupt technology on Earth.",
        ],
        subtitle_text=narration,
    )
    prompts: list[str] = []
    monkeypatch.setattr(
        service,
        "_generate_model",
        lambda *, prompt, **kwargs: prompts.append(prompt) or content,
    )
    original_validate = service.validate_short_content
    calls = 0

    def fail_once(value, selected_topic) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("payoff must directly answer the hook")
        original_validate(value, selected_topic)

    monkeypatch.setattr(service, "validate_short_content", fail_once)

    service.generate_short_content(
        topic=topic,
        excluded_titles=[],
        prompt_path=tmp_path / "prompt.txt",
        response_path=tmp_path / "response.json",
    )

    assert "payoff must directly answer the hook" in prompts[1]
    assert "Previous draft:" in prompts[1]


def test_read_timeout_is_not_retried(tmp_path) -> None:
    calls = 0

    class FakeClient:
        def post(self, path: str, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("generation stalled")

    service = OllamaService(Settings(_env_file=None, retry_attempts=3))
    service.client = FakeClient()

    with pytest.raises(PipelineStageError, match="read timeout"):
        service._generate_model(
            prompt="irrelevant",
            stage="content_generation",
            prompt_output_path=tmp_path / "content_generation.json",
            model_cls=TopicChoice,
        )

    assert calls == 1


def test_long_section_repair_makes_progress_when_extension_repeats(monkeypatch) -> None:
    service = OllamaService(Settings(_env_file=None))
    monkeypatch.setattr(
        service,
        "_reason_extension",
        lambda topic, focus, index, *, test_mode: "The same reason is repeated.",
    )

    narration = service._fit_section_words(
        "A short opening.",
        "Minecraft",
        1,
        focus="Blocks",
        minimum_words=40,
        maximum_words=50,
        test_mode=False,
    )

    assert len(narration.split()) >= 40


def test_short_normalization_caps_hook_and_payoff_beats() -> None:
    service = OllamaService(Settings(_env_file=None, mock_mode=True))
    topic = TopicChoice(
        bucket="space",
        topic="the Sun",
        visual_queries=["the Sun", "solar flare"],
        search_terms=["the Sun"],
    )
    content = GeneratedShort(
        bucket="space",
        topic="the Sun",
        title="THE SUN HIDES A VIOLENT SECRET",
        description="A specific visual story about the Sun's magnetic storms and the flares they create.",
        hashtags=["#Sun", "#Space", "#SolarScience"],
        narration=(
            "The Sun hides violent magnetic storms above its bright surface. "
            "Those fields twist and store energy before releasing it as a flare. "
            "The flare can disrupt technology on Earth. "
            "That hidden magnetic energy is what makes the calm surface dangerous."
        ),
        facts=[
            "The Sun's surface hides magnetic storms.",
            "Twisted magnetic fields can release energy as a flare.",
            "Solar flares can disrupt technology on Earth.",
        ],
        subtitle_text="The Sun hides violent magnetic storms above its bright surface.",
        beats=[
            {"beat_type": "hook", "narration": "The Sun hides violent magnetic storms above its bright surface today", "visual_query": "Sun surface", "on_screen_text": "MAGNETIC STORMS"},
            {"beat_type": "evidence", "narration": "Those invisible fields twist and store energy before releasing it as a flare across the Sun's surface.", "visual_query": "solar flare", "on_screen_text": "ENERGY BUILDS"},
            {"beat_type": "escalation", "narration": "The flare can disrupt technology on Earth, because charged particles race through space and reach our satellites.", "visual_query": "solar flare Earth", "on_screen_text": "EARTH FEELS IT"},
            {"beat_type": "payoff", "narration": "That hidden magnetic energy is what makes the calm surface dangerous for our technology and satellites.", "visual_query": "Sun magnetic field", "on_screen_text": "HIDDEN ENERGY"},
        ],
    )

    normalized = service._normalize_generated_short(content, topic)

    assert len(normalized.beats[0].narration.split()) <= 16
    assert len(normalized.beats[-1].narration.split()) <= 16


def test_short_repair_discards_beats_that_are_shorter_than_repaired_narration() -> None:
    service = OllamaService(Settings(_env_file=None))
    response = {
        "bucket": "inventions",
        "topic": "Velcro",
        "title": "THE STRANGE INVENTION THAT CHANGED THE WORLD",
        "description": "A specific story about how Velcro turned a natural fastening trick into a useful invention.",
        "hashtags": ["#Velcro", "#Inventions", "#Science"],
        "narration": "Velcro was inspired by burrs that stuck to clothing during a hunting trip. Its hooks and loops copy that natural trick, which makes the material useful for fastening shoes, clothing, and equipment.",
        "facts": [
            "Velcro was inspired by burrs that stuck to clothing.",
            "Its hooks and loops copy a natural fastening trick.",
            "Velcro is used for shoes, clothing, and equipment.",
        ],
        "subtitle_text": "Velcro was inspired by burrs that stuck to clothing.",
        "beats": [
            {"beat_type": "hook", "narration": "Velcro was inspired by burrs.", "visual_query": "burr on clothing"},
            {"beat_type": "evidence", "narration": "Its hooks copy nature.", "visual_query": "Velcro hooks"},
            {"beat_type": "escalation", "narration": "The trick became useful.", "visual_query": "Velcro fastening"},
            {"beat_type": "payoff", "narration": "It fastens shoes and clothing.", "visual_query": "Velcro shoes"},
        ],
    }

    repaired = service._repair_model_response(
        response_text=json.dumps(response),
        model_cls=GeneratedShort,
    )

    assert repaired is not None
    assert 55 <= len(repaired.narration.split()) <= 90
