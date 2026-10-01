from __future__ import annotations

import json

import httpx
import pytest

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.models import GeneratedLongVideo, GeneratedShort, TopicChoice
from youtube_kanaal.models.content import LongNarrationRevision
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


def test_long_form_retries_a_short_invalid_draft_with_a_repair_prompt(tmp_path) -> None:
    topic = TopicChoice(
        bucket="history",
        topic="the Library of Alexandria",
        visual_queries=["Library of Alexandria", "ancient library"],
        search_terms=["Library of Alexandria"],
    )
    valid_content = OllamaService(Settings(mock_mode=True))._fallback_long_content(topic)
    invalid_payload = valid_content.model_dump(mode="json")
    invalid_payload["title"] = "The Mysterious Case of the Lost Knowledge: Uncovering the Secrets of the Library of Alexandria"
    invalid_payload["sections"] = [
        {
            **section,
            "narration": "A short chapter about the Library of Alexandria.",
        }
        for section in invalid_payload["sections"]
    ]
    responses = iter(
        [
            {"response": json.dumps(invalid_payload)},
            {"response": valid_content.model_dump_json()},
        ]
    )
    captured_prompts: list[str] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, str]) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return self.payload

    class FakeClient:
        def post(self, path: str, **kwargs: object) -> FakeResponse:
            captured_prompts.append(kwargs["json"]["prompt"])
            return FakeResponse(next(responses))

    service = OllamaService(Settings(_env_file=None))
    service.client = FakeClient()
    result = service._generate_model(
        prompt="initial prompt",
        stage="long_content_generation",
        prompt_output_path=tmp_path / "long_content_generation.json",
        model_cls=GeneratedLongVideo,
    )

    assert result.title == valid_content.title
    assert len(captured_prompts) == 2
    assert "7-9 chapters" in captured_prompts[1]


def test_long_form_expands_a_chapter_with_short_continuations() -> None:
    service = OllamaService(Settings(_env_file=None))
    continuations = [
        " ".join(
            f"Detail {batch} explains a new historical clue for chapter {index} today carefully."
            for index in range(1, 7)
        )
        for batch in range(1, 6)
    ]

    class FakeResponse:
        def __init__(self, continuation: str) -> None:
            self.continuation = continuation

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"response": json.dumps({"continuation": self.continuation})}

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, path: str, **kwargs: object) -> FakeResponse:
            continuation = continuations[self.calls]
            self.calls += 1
            return FakeResponse(continuation)

    client = FakeClient()
    service.client = client
    initial = "The library became a meeting point for scholars who compared texts and debated ideas."

    narration, queries = service._expand_long_section(
        topic="the Library of Alexandria",
        title="The Founding of the Library",
        narration=initial,
        visual_queries=["ancient library", "Alexandria harbor"],
    )

    assert 315 <= len(narration.split()) <= 390
    assert len(narration) <= 2500
    assert queries == ["ancient library", "Alexandria harbor"]
    assert client.calls == 5


def test_long_form_expands_past_eight_short_continuations() -> None:
    service = OllamaService(Settings(_env_file=None))
    continuations = [
        f"Detail {index} adds useful mechanism context here today."
        for index in range(8)
    ] + [
        "This final detail explains how the mechanism changes the outcome in a concrete example."
    ]

    class FakeResponse:
        def __init__(self, continuation: str) -> None:
            self.continuation = continuation

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"response": json.dumps({"continuation": self.continuation})}

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, path: str, **kwargs: object) -> FakeResponse:
            continuation = continuations[self.calls]
            self.calls += 1
            return FakeResponse(continuation)

    client = FakeClient()
    service.client = client
    initial = " ".join(f"Detail {index} explains this mechanism clearly today." for index in range(35))

    narration, _ = service._expand_long_section(
        topic="the immune system",
        title="Immune System",
        narration=initial,
        visual_queries=["immune system"],
    )

    assert 315 <= len(narration.split()) <= 390
    assert client.calls == 9


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


def test_illustrated_bridge_test_uses_canonical_chapter_plan(tmp_path, monkeypatch) -> None:
    service = OllamaService(Settings(_env_file=None))
    monkeypatch.setattr(service, "_generate_model", lambda **kwargs: pytest.fail("bridge forms are curated"))
    topic = TopicChoice(
        bucket="architecture",
        topic="bridges",
        visual_queries=["bridges", "bridge load paths"],
        search_terms=["bridge types"],
    )

    subjects = service.generate_long_chapter_plan(
        topic=topic,
        prompt_path=tmp_path / "chapter_plan.txt",
        response_path=tmp_path / "chapter_plan.json",
        target_duration_seconds=90,
        visual_style="illustrated_explainer",
    )

    assert subjects == [
        "Beam Bridge",
        "Arch Bridge",
        "Truss Bridge",
        "Suspension Bridge",
        "Cable-Stayed Bridge",
        "Cantilever Bridge",
    ]
    saved = json.loads((tmp_path / "chapter_plan.json").read_text(encoding="utf-8"))
    assert saved["selection_method"] == "canonical bridge-form sequence"


def test_illustrated_test_retries_a_short_script_without_padding(tmp_path, monkeypatch) -> None:
    service = OllamaService(Settings(_env_file=None))
    topic = TopicChoice(
        bucket="architecture",
        topic="bridges",
        visual_queries=["bridges", "bridge load paths"],
        search_terms=["bridge types"],
    )
    subjects = ["Beam Bridge", "Arch Bridge", "Truss Bridge", "Suspension Bridge", "Cable-Stayed Bridge", "Cantilever Bridge"]
    short_payload = {
        "bucket": "architecture",
        "topic": "bridges",
        "title": "How Six Bridge Shapes Carry Traffic",
        "thumbnail_text": "BRIDGE LOAD PATHS",
        "intro": "Why can bridges with different shapes carry the same traffic safely?",
        "description": "A structural explainer about six bridge forms and their distinct load paths. It compares how tension and compression move through each design and why span and ground conditions affect the choice.",
        "tags": ["bridges", "architecture", "loads", "structures", "engineering", "design", "physics", "explainer"],
        "duration_profile": "test",
        "facts": [f"Distinct sourced bridge fact {index}." for index in range(1, 7)],
        "sections": [],
    }

    def section_payload(subject: str, narration: str) -> dict[str, object]:
        return {
            "chapter_subject": subject,
            "title": subject,
            "narration": narration,
            "visual_queries": [f"{subject} diagram", f"{subject} load path"],
            "visual_plan": {
                "kind": "bridge",
                "variant": subject.casefold(),
                "elements": ["deck", "support", "load path"],
                "relation": f"{subject} transfers deck loads toward its supports",
            },
        }

    short_payload["sections"] = [
        section_payload(subject, f"{subject} carries traffic loads through its structure toward supports, and engineers compare that path before selection.")
        for subject in subjects
    ]
    detailed_narration = (
        "This form carries traffic loads from its deck through the main structure toward stable supports. "
        "Its geometry guides tension and compression along a distinct path. Engineers compare span, materials, "
        "and ground conditions before choosing it, because those constraints change where forces collect."
    )
    responses = iter(
        [
            GeneratedLongVideo.model_validate(short_payload),
            LongNarrationRevision(
                intro="Why do bridge shapes change where forces travel?",
                narrations=[detailed_narration] * 6,
            ),
        ]
    )
    prompts: list[str] = []

    def generate(*, prompt: str, prompt_output_path, **kwargs):
        prompts.append(prompt)
        content = next(responses)
        prompt_output_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_output_path.write_text(json.dumps({"response": content.model_dump_json()}), encoding="utf-8")
        return content

    monkeypatch.setattr(service, "_generate_model", generate)

    result = service.generate_long_content(
        topic=topic,
        excluded_titles=[],
        prompt_path=tmp_path / "long_content_generation.txt",
        response_path=tmp_path / "long_content_generation.json",
        target_duration_seconds=90,
        chapter_subjects=subjects,
        visual_style="illustrated_explainer",
        target_speech_rate_wpm=150,
    )

    assert len(prompts) == 2
    assert "Revise only the spoken copy" in prompts[1]
    assert "Current spoken copy" in prompts[1]
    assert '"description"' not in prompts[1]
    assert len(result.narration.split()) in range(185, 266)
    assert "the chapter" not in result.narration.casefold()
    assert (tmp_path / "long_content_generation_attempt_1.json").exists()


def test_measured_duration_revision_reuses_package_and_requests_only_spoken_copy(tmp_path, monkeypatch) -> None:
    service = OllamaService(Settings(_env_file=None))
    topic = TopicChoice(
        bucket="architecture",
        topic="bridges",
        visual_queries=["bridges", "bridge load paths"],
        search_terms=["bridge types"],
    )
    subjects = ["Beam Bridge", "Arch Bridge", "Truss Bridge", "Suspension Bridge", "Cable-Stayed Bridge", "Cantilever Bridge"]
    full_content = service._fallback_test_long_content(topic, chapter_subjects=subjects)
    short_prior = GeneratedLongVideo.model_validate(
        {
            **full_content.model_dump(mode="python"),
            "intro": "Why can six bridge forms carry traffic differently?",
            "sections": [
                {
                    **section.model_dump(mode="python"),
            "narration": (
                "This form transfers traffic from the deck through its structure into stable supports, "
                "while its shape guides tension and compression."
            ),
                }
                for section in full_content.sections
            ],
        }
    )
    revision = LongNarrationRevision(
        intro=full_content.intro,
        narrations=[section.narration for section in full_content.sections],
    )
    captured: dict[str, object] = {}

    def generate(*, prompt: str, prompt_output_path, model_cls, **kwargs):
        captured["prompt"] = prompt
        captured["model_cls"] = model_cls
        prompt_output_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_output_path.write_text(json.dumps({"response": revision.model_dump_json()}), encoding="utf-8")
        return revision

    monkeypatch.setattr(service, "_generate_model", generate)
    response_path = tmp_path / "long_content_generation.json"
    response_path.write_text(json.dumps(short_prior.model_dump(mode="json")), encoding="utf-8")

    result = service.generate_long_content(
        topic=topic,
        excluded_titles=[],
        prompt_path=tmp_path / "long_content_generation.txt",
        response_path=response_path,
        target_duration_seconds=90,
        chapter_subjects=subjects,
        visual_style="illustrated_explainer",
        target_speech_rate_wpm=166,
        prior_content=short_prior,
        revision_reason="Measured narration was 66.8 seconds; target is 90 seconds.",
    )

    assert captured["model_cls"] is LongNarrationRevision
    assert "Measured narration was 66.8 seconds" in captured["prompt"]
    assert '"description"' not in captured["prompt"]
    assert result.title == short_prior.title
    assert result.sections[0].visual_plan == short_prior.sections[0].visual_plan
    assert len(result.narration.split()) in range(204, 295)
    assert (tmp_path / "long_content_generation_attempt_1.json").exists()


def test_reviewed_script_normalization_keeps_content_and_checks_topic() -> None:
    service = OllamaService(Settings(_env_file=None))
    topic = TopicChoice(
        bucket="architecture",
        topic="bridges",
        visual_queries=["bridges", "bridge load paths"],
        search_terms=["bridge types"],
    )
    subjects = ["Beam Bridge", "Arch Bridge", "Truss Bridge", "Suspension Bridge", "Cable-Stayed Bridge", "Cantilever Bridge"]
    content = service._fallback_test_long_content(topic, chapter_subjects=subjects)
    references = [{"title": "Bridge guide", "publisher": "Example source", "url": "https://example.test/bridges", "supports": "bridge forms"}]

    normalized = service.normalize_reviewed_long_content(
        content,
        topic,
        target_duration_seconds=90,
        visual_style="illustrated_explainer",
        chapter_subjects=subjects,
        target_speech_rate_wpm=150,
        verified_source_notes=references,
    )

    assert normalized.narration == content.narration
    assert [section.chapter_subject for section in normalized.sections] == subjects
    assert normalized.sources[0].url == references[0]["url"]
    with pytest.raises(ValueError, match="topic and bucket must match"):
        service.normalize_reviewed_long_content(
            content,
            topic.model_copy(update={"topic": "the Nile River", "bucket": "geography"}),
            target_duration_seconds=90,
            visual_style="illustrated_explainer",
            chapter_subjects=subjects,
        )
