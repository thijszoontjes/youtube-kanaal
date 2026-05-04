from __future__ import annotations

import json
import re
from itertools import chain
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from pydantic_core import ValidationError as CoreValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.models.content import GeneratedShort, TOPIC_CATALOG, TopicChoice
from youtube_kanaal.prompts import build_content_generation_prompt, build_topic_selection_prompt
from youtube_kanaal.utils.files import write_json, write_text

TModel = TypeVar("TModel", bound=BaseModel)
_VALIDATION_ERROR_TYPES = (ValidationError, CoreValidationError)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_STOCK_OUTRO_PREFIXES: tuple[str, ...] = (
    "that is why ",
    "that's why ",
    "people remember ",
)
_STOCK_OUTRO_FRAGMENTS: tuple[str, ...] = (
    "looks so unusual on screen",
    "works so well in a fast visual short",
    "keeps showing up in science videos and documentaries",
)
_FORMULAIC_NARRATION_PREFIXES: tuple[str, ...] = (
    "here are ",
    "first,",
    "first:",
    "first up,",
    "second,",
    "second:",
    "third,",
    "third:",
    "fact 1",
    "fact one",
    "fact 2",
    "fact two",
    "fact 3",
    "fact three",
)
_GENERIC_TITLE_RE = re.compile(
    r"^\s*(?:3|three)\s+(?:quick\s+|wild\s+|surprising\s+|amazing\s+|interesting\s+)?facts\s+about\b",
    re.IGNORECASE,
)


class OllamaService:
    """Local Ollama client for topic and content generation."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.ollama_base_url,
            timeout=self.settings.ollama_timeout_seconds,
        )

    def list_models(self) -> list[str]:
        if self.settings.mock_mode:
            return [self.settings.ollama_model]
        try:
            response = self.client.get("/api/tags")
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError:
            return []
        return [model["name"] for model in payload.get("models", [])]

    def is_available(self) -> bool:
        return bool(self.list_models())

    def choose_topic(
        self,
        *,
        excluded_topics: list[str],
        prompt_path: Path,
        response_path: Path,
    ) -> TopicChoice:
        if self.settings.mock_mode:
            choice = self._fallback_topic(excluded_topics)
            write_text(prompt_path, "mock-mode topic selection")
            write_json(response_path, choice.model_dump(mode="json"))
            return choice
        prompt = build_topic_selection_prompt(excluded_topics)
        write_text(prompt_path, prompt)
        return self._generate_model(
            prompt=prompt,
            stage="topic_selection",
            prompt_output_path=response_path,
            model_cls=TopicChoice,
        )

    def generate_short_content(
        self,
        *,
        topic: TopicChoice,
        excluded_titles: list[str],
        prompt_path: Path,
        response_path: Path,
    ) -> GeneratedShort:
        if self.settings.mock_mode:
            content = self._fallback_content(topic)
            write_text(prompt_path, "mock-mode content generation")
            write_json(response_path, content.model_dump(mode="json"))
            return content
        prompt = build_content_generation_prompt(topic, excluded_titles)
        write_text(prompt_path, prompt)
        content = self._generate_model(
            prompt=prompt,
            stage="content_generation",
            prompt_output_path=response_path,
            model_cls=GeneratedShort,
        )
        return self._normalize_generated_short(content, topic)

    def _generate_model(
        self,
        *,
        prompt: str,
        stage: str,
        prompt_output_path: Path,
        model_cls: type[TModel],
    ) -> TModel:
        @retry(
            stop=stop_after_attempt(self.settings.retry_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type((httpx.HTTPError, ValueError, *_VALIDATION_ERROR_TYPES)),
            reraise=True,
        )
        def _request() -> TModel:
            response = self.client.post(
                "/api/generate",
                json={
                    "model": self.settings.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "keep_alive": 0,
                },
            )
            response.raise_for_status()
            payload = response.json()
            write_json(prompt_output_path, payload)
            response_text = payload.get("response", "").strip()
            if not response_text:
                raise ValueError("Ollama returned an empty response.")
            try:
                return model_cls.model_validate_json(response_text)
            except _VALIDATION_ERROR_TYPES:
                repaired = self._repair_model_response(response_text=response_text, model_cls=model_cls)
                if repaired is not None:
                    return repaired
                raise

        try:
            return _request()
        except (httpx.HTTPError, ValueError, *_VALIDATION_ERROR_TYPES) as exc:
            raise PipelineStageError(
                stage=stage,
                message="Failed to generate valid JSON from Ollama.",
                probable_cause=str(exc),
                details_path=prompt_output_path,
            ) from exc

    def _repair_model_response(self, *, response_text: str, model_cls: type[TModel]) -> TModel | None:
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None

        repaired_payload = dict(payload)
        catalog_match = self._resolve_catalog_topic(str(repaired_payload.get("topic", "")))
        if catalog_match is not None:
            bucket, canonical_topic = catalog_match
            repaired_payload["bucket"] = bucket
            repaired_payload["topic"] = canonical_topic
        elif model_cls is TopicChoice:
            bucket_match = self._resolve_bucket_candidate(
                bucket=str(repaired_payload.get("bucket", "")),
                topic=str(repaired_payload.get("topic", "")),
            )
            if bucket_match is not None:
                bucket, canonical_topic = bucket_match
                repaired_payload["bucket"] = bucket
                repaired_payload["topic"] = canonical_topic

        if model_cls is TopicChoice:
            topic_value = str(repaired_payload.get("topic", "")).strip()
            bucket_value = str(repaired_payload.get("bucket", "")).strip()
            visual_queries = repaired_payload.get("visual_queries")
            cleaned_visuals = []
            if isinstance(visual_queries, list):
                cleaned_visuals = [str(item).strip() for item in visual_queries if str(item).strip()]
            if len(cleaned_visuals) < 2:
                cleaned_visuals.extend([topic_value, f"{topic_value} close up", bucket_value])
            repaired_payload["visual_queries"] = list(dict.fromkeys(cleaned_visuals))[:5]
            search_terms = repaired_payload.get("search_terms")
            cleaned_terms = []
            if isinstance(search_terms, list):
                cleaned_terms = [str(item).strip() for item in search_terms if str(item).strip()]
            if not cleaned_terms:
                cleaned_terms.extend([topic_value, f"{topic_value} {bucket_value}", bucket_value])
            repaired_payload["search_terms"] = list(dict.fromkeys(cleaned_terms))[:6]
        elif model_cls is GeneratedShort:
            repaired_payload = self._repair_generated_short_payload(repaired_payload)

        try:
            return model_cls.model_validate(repaired_payload)
        except _VALIDATION_ERROR_TYPES:
            return None

    def _resolve_catalog_topic(self, topic: str) -> tuple[str, str] | None:
        normalized = topic.strip().lower()
        if not normalized:
            return None
        for bucket, topics in TOPIC_CATALOG.items():
            for candidate in topics:
                if candidate.lower() == normalized:
                    return bucket, candidate
        return None

    def _resolve_bucket_candidate(self, *, bucket: str, topic: str) -> tuple[str, str] | None:
        candidates = [bucket.strip().lower(), topic.strip().lower()]
        for candidate in candidates:
            if candidate in TOPIC_CATALOG:
                return candidate, TOPIC_CATALOG[candidate][0]
        return None

    def _repair_generated_short_payload(self, payload: dict[str, object]) -> dict[str, object]:
        repaired = dict(payload)
        topic_value = str(repaired.get("topic", "")).strip()
        bucket_value = str(repaired.get("bucket", "")).strip().lower()

        catalog_match = self._resolve_catalog_topic(topic_value)
        if catalog_match is not None:
            bucket_value, topic_value = catalog_match
        else:
            bucket_match = self._resolve_bucket_candidate(bucket=bucket_value, topic=topic_value)
            if bucket_match is not None:
                bucket_value, topic_value = bucket_match

        repaired["bucket"] = bucket_value
        repaired["topic"] = topic_value

        description = str(repaired.get("description", "")).strip()
        if len(description) < 40:
            description = f"Three fast facts about {topic_value} for a visual YouTube Short made locally."
        repaired["description"] = description

        hashtags = repaired.get("hashtags")
        cleaned_hashtags = []
        if isinstance(hashtags, list):
            cleaned_hashtags = [str(tag).strip() for tag in hashtags if str(tag).strip()]
        cleaned_hashtags.extend(["#shorts", "#facts", f"#{topic_value.title().replace(' ', '')}"])
        repaired["hashtags"] = list(dict.fromkeys(cleaned_hashtags))[:8]

        raw_narration = self._clean_narration(str(repaired.get("narration", "")))

        facts = repaired.get("facts")
        cleaned_facts = []
        if isinstance(facts, list):
            cleaned_facts = [self._normalize_sentence(str(fact)) for fact in facts if str(fact).strip()]
        if len(cleaned_facts) < 3:
            cleaned_facts.extend(self._facts_from_narration(raw_narration, topic_value))
        if len(cleaned_facts) < 3:
            cleaned_facts.extend(self._fallback_facts(topic_value, bucket_value))
        repaired["facts"] = self._dedupe_preserving_order(cleaned_facts)[:3]

        narration = raw_narration
        repaired_facts = list(repaired["facts"]) if isinstance(repaired["facts"], list) else []
        if not narration and repaired_facts:
            narration = self._build_narration(topic_value, repaired_facts)
        elif repaired_facts and not 45 <= len(narration.split()) <= 90:
            narration = self._fit_narration_to_duration(narration, repaired_facts, topic_value)
        repaired["narration"] = narration

        title = str(repaired.get("title", "")).strip()
        title_hook = str(repaired.get("title_hook", "")).strip()
        repaired["title_hook"] = self._select_title(
            title=title_hook,
            title_hook=title,
            topic=topic_value,
            facts=repaired_facts,
        )
        repaired["title"] = self._select_title(
            title=title,
            title_hook=title_hook,
            topic=topic_value,
            facts=repaired_facts,
        )
        repaired["hook_text"] = self._select_hook_text(
            str(repaired.get("hook_text", "")).strip(),
            topic=topic_value,
            title=str(repaired["title"]),
            facts=repaired_facts,
        )

        repaired["subtitle_text"] = narration
        return repaired

    def _dedupe_preserving_order(self, values: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = self._normalize_sentence(value)
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            deduped.append(normalized)
            seen.add(key)
        return deduped

    def _facts_from_narration(self, narration: str, topic: str) -> list[str]:
        sentences = [self._normalize_sentence(sentence) for sentence in _SENTENCE_SPLIT_RE.split(narration) if sentence.strip()]
        facts: list[str] = []
        for sentence in sentences:
            lowered = sentence.lower()
            if len(sentence.split()) < 5:
                continue
            if lowered.startswith(("here are ", "these are ")):
                continue
            facts.append(sentence)
        if len(facts) >= 3:
            return facts[:3]
        topic_lower = topic.lower()
        for sentence in sentences:
            if topic_lower in sentence.lower() and sentence not in facts:
                facts.append(sentence)
            if len(facts) >= 3:
                break
        return facts[:3]

    def _fallback_facts(self, topic: str, bucket: str) -> list[str]:
        bucket_label = bucket or "science and culture"
        return [
            f"{topic} has details that make it useful for a fast visual explainer.",
            f"{topic} connects to {bucket_label} in ways that can be shown clearly on screen.",
            f"{topic} includes enough real-world context to support a short three-point story.",
        ]

    def _fit_narration_to_duration(self, narration: str, facts: list[str], topic: str) -> str:
        sentences = [sentence.strip() for sentence in _SENTENCE_SPLIT_RE.split(narration) if sentence.strip()]
        if sentences:
            kept: list[str] = []
            for sentence in sentences:
                candidate = " ".join([*kept, sentence]).strip()
                if len(candidate.split()) > 90:
                    break
                kept.append(sentence)
            candidate = " ".join(kept).strip()
            if len(candidate.split()) >= 45:
                return candidate
        return self._build_narration(topic, facts)

    def _normalize_generated_short(self, content: GeneratedShort, topic: TopicChoice) -> GeneratedShort:
        facts = [self._normalize_sentence(fact) for fact in content.facts]
        narration_candidates: list[str] = []
        cleaned_narration = self._clean_narration(content.narration)
        if topic.topic.lower() in cleaned_narration.lower():
            narration_candidates.append(cleaned_narration)
        narration_candidates.append(self._build_narration(topic.topic, facts))

        title = self._select_title(
            title=content.title,
            title_hook=content.title_hook or "",
            topic=topic.topic,
            facts=facts,
        )
        title_hook = self._select_title(
            title=content.title_hook or "",
            title_hook=content.title,
            topic=topic.topic,
            facts=facts,
        )
        hook_text = self._select_hook_text(content.hook_text or "", topic=topic.topic, title=title, facts=facts)

        base_payload = content.model_dump(mode="json")
        base_payload.update(
            {
                "bucket": topic.bucket,
                "topic": topic.topic,
                "title": title,
                "title_hook": title_hook,
                "hook_text": hook_text,
                "facts": facts,
            }
        )

        for narration in narration_candidates:
            candidate_payload = dict(base_payload)
            candidate_payload["narration"] = narration
            candidate_payload["subtitle_text"] = narration
            try:
                return GeneratedShort.model_validate(candidate_payload)
            except _VALIDATION_ERROR_TYPES:
                continue

        base_payload["subtitle_text"] = content.narration
        return GeneratedShort.model_validate(base_payload)

    def _normalize_sentence(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            return cleaned
        if cleaned[-1] not in ".!?":
            cleaned = f"{cleaned}."
        return cleaned

    def _clean_narration(self, narration: str) -> str:
        cleaned = " ".join(narration.split()).strip()
        if not cleaned:
            return cleaned
        sentences = _SENTENCE_SPLIT_RE.split(cleaned)
        filtered_sentences = []
        for sentence in sentences:
            stripped = sentence.strip()
            lowered = stripped.lower()
            if any(lowered.startswith(prefix) for prefix in _FORMULAIC_NARRATION_PREFIXES):
                continue
            if any(lowered.startswith(prefix) for prefix in _STOCK_OUTRO_PREFIXES):
                continue
            if any(fragment in lowered for fragment in _STOCK_OUTRO_FRAGMENTS):
                continue
            filtered_sentences.append(stripped)
        return " ".join(filtered_sentences).strip()

    def _select_title(self, *, title: str, title_hook: str, topic: str, facts: list[str]) -> str:
        cleaned_title = " ".join(title.split()).strip()
        cleaned_hook = " ".join(title_hook.split()).strip()
        if cleaned_hook and self._is_better_title(cleaned_hook, cleaned_title):
            return cleaned_hook
        if cleaned_title and not self._is_generic_title(cleaned_title):
            return cleaned_title
        return self._fallback_title(topic, facts)

    def _is_better_title(self, candidate: str, current: str) -> bool:
        if not candidate or len(candidate) > 70:
            return False
        if self._is_generic_title(candidate):
            return False
        if not current:
            return True
        return self._is_generic_title(current) or len(candidate) < len(current) + 18

    def _is_generic_title(self, title: str) -> bool:
        lowered = title.lower()
        return bool(_GENERIC_TITLE_RE.search(lowered)) or ": 3 facts" in lowered or "3 facts you should know" in lowered

    def _fallback_title(self, topic: str, facts: list[str]) -> str:
        topic_title = topic[:1].upper() + topic[1:]
        fact_text = " ".join(facts).lower()
        if "ocean" in fact_text or "underwater" in fact_text or "sea" in fact_text:
            candidates = [
                f"{topic_title} Shouldn't Exist Like This",
                f"The Ocean Secret Behind {topic_title}",
                f"What {topic_title} Is Hiding",
            ]
        elif "space" in fact_text or "planet" in fact_text or "moon" in fact_text:
            candidates = [
                f"{topic_title} Is Stranger Than It Looks",
                f"The Space Detail Everyone Misses About {topic_title}",
                f"What {topic_title} Is Hiding",
            ]
        else:
            candidates = [
                f"{topic_title} Is Stranger Than It Looks",
                f"What {topic_title} Is Hiding",
                f"Nobody Expects This About {topic_title}",
            ]
        for candidate in candidates:
            if 15 <= len(candidate) <= 70:
                return candidate
        return f"The Weird Truth About {topic_title}"[:70].rstrip()

    def _select_hook_text(self, hook_text: str, *, topic: str, title: str, facts: list[str]) -> str:
        cleaned = " ".join(hook_text.split()).strip(" .")
        if 8 <= len(cleaned) <= 54 and len(cleaned.split()) <= 9:
            return cleaned
        title_hook = title.strip(" .")
        if 8 <= len(title_hook) <= 54 and len(title_hook.split()) <= 9:
            return title_hook
        topic_title = topic[:1].upper() + topic[1:]
        fact_text = " ".join(facts).lower()
        if "underwater" in fact_text or "ocean" in fact_text:
            return "This should not exist underwater"
        if "space" in fact_text or "planet" in fact_text:
            return "This space detail is unreal"
        return f"{topic_title} gets weird fast"[:54].rstrip()

    def _variation_seed(self, topic: str, facts: list[str]) -> int:
        return sum(ord(char) for char in topic.lower()) + sum(len(fact) for fact in facts)

    def _build_narration(self, topic: str, facts: list[str]) -> str:
        normalized_facts = [self._normalize_sentence(fact) for fact in facts[:3]]
        if len(normalized_facts) < 3:
            return f"{topic} has a few details that are worth a closer look."

        seed = self._variation_seed(topic, normalized_facts)
        intros = [
            f"Why is {topic} so much stranger than it looks?",
            f"{topic} should be ordinary, but it really is not.",
            f"Okay, something genuinely weird is hiding in {topic}.",
            f"What makes {topic} worth stopping for is the part most people miss.",
        ]
        first_leads = [
            f"For one thing, {normalized_facts[0]}",
            f"To start, {normalized_facts[0]}",
            f"Right away, {normalized_facts[0]}",
            f"The opening detail is already odd: {normalized_facts[0]}",
        ]
        second_leads = [
            f"Then there's this: {normalized_facts[1]}",
            f"Also, {normalized_facts[1]}",
            f"And it gets better, because {normalized_facts[1]}",
            f"Another thing, {normalized_facts[1]}",
        ]
        third_leads = [
            f"And honestly, {normalized_facts[2]}",
            f"And maybe the weirdest part, {normalized_facts[2]}",
            f"Oh, and {normalized_facts[2]}",
            f"And somehow, {normalized_facts[2]}",
        ]
        closers = [
            "Kind of a ridiculous combo, really.",
            "Not exactly what most people expect.",
            "Which is a lot to pack into one topic, honestly.",
            "Yeah, that's a weird little stack of facts.",
        ]
        narration = " ".join(
            [
                intros[seed % len(intros)],
                first_leads[(seed + 1) % len(first_leads)],
                second_leads[(seed + 2) % len(second_leads)],
                third_leads[(seed + 3) % len(third_leads)],
                closers[(seed + 4) % len(closers)],
            ]
        ).strip()
        if len(narration.split()) < 45:
            extra_beats = [
                "Even on its own, that would already be enough to make somebody stop scrolling for a second.",
                "And honestly, once those details stack up, the whole thing feels a lot less ordinary.",
                "Which is kind of wild, because each part sounds made up until you remember it's real.",
            ]
            narration = f"{narration} {extra_beats[(seed + 5) % len(extra_beats)]}".strip()
        return narration

    def _fallback_topic(self, excluded_topics: list[str]) -> TopicChoice:
        excluded = {item.lower() for item in excluded_topics}
        for bucket, topic in chain.from_iterable(
            ([(bucket, topic) for topic in topics] for bucket, topics in TOPIC_CATALOG.items())
        ):
            if topic.lower() not in excluded:
                return TopicChoice(
                    bucket=bucket,
                    topic=topic,
                    visual_queries=[topic, f"{topic} close up", bucket],
                    search_terms=[topic, f"{topic} {bucket}", bucket],
                )
        bucket = next(iter(TOPIC_CATALOG))
        topic = TOPIC_CATALOG[bucket][0]
        return TopicChoice(
            bucket=bucket,
            topic=topic,
            visual_queries=[topic, f"{topic} nature", bucket],
            search_terms=[topic, f"{topic} {bucket}", bucket],
        )

    def _fallback_content(self, topic: TopicChoice) -> GeneratedShort:
        facts = [
            f"{topic.topic.title()} can appear in places people rarely expect.",
            f"Scientists study {topic.topic.lower()} because it reveals useful patterns in nature.",
            f"{topic.topic.title()} is visually striking, which makes it perfect for a short explainer.",
        ]
        narration = self._build_narration(topic.topic, facts)
        fallback_title = self._fallback_title(topic.topic, facts)
        return GeneratedShort(
            bucket=topic.bucket,
            topic=topic.topic,
            title=fallback_title,
            title_hook=fallback_title,
            hook_text=self._select_hook_text("", topic=topic.topic, title=fallback_title, facts=facts),
            description=(
                f"Three fast facts about {topic.topic} for a visual YouTube Short. "
                "Generated locally with Ollama, Piper, whisper.cpp, FFmpeg, and Pexels."
            ),
            hashtags=["#shorts", "#facts", f"#{topic.topic.title().replace(' ', '')}"],
            narration=narration,
            facts=facts,
            subtitle_text=narration,
        )
