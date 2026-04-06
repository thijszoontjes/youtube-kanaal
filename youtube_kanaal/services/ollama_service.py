from __future__ import annotations

import json
from itertools import chain
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.models.content import GeneratedShort, TOPIC_CATALOG, TopicChoice
from youtube_kanaal.prompts import build_content_generation_prompt, build_topic_selection_prompt
from youtube_kanaal.utils.files import write_json, write_text

TModel = TypeVar("TModel", bound=BaseModel)


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
        return self._generate_model(
            prompt=prompt,
            stage="content_generation",
            prompt_output_path=response_path,
            model_cls=GeneratedShort,
        )

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
            retry=retry_if_exception_type((httpx.HTTPError, ValidationError, ValueError)),
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
            except ValidationError:
                repaired = self._repair_model_response(response_text=response_text, model_cls=model_cls)
                if repaired is not None:
                    return repaired
                raise

        try:
            return _request()
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
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

        try:
            return model_cls.model_validate(repaired_payload)
        except ValidationError:
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
        narration = (
            f"Here are three facts about {topic.topic}. "
            f"Fact one: {facts[0]} "
            f"Fact two: {facts[1]} "
            f"Fact three: {facts[2]} "
            f"That is why {topic.topic} keeps showing up in science videos and documentaries."
        )
        return GeneratedShort(
            bucket=topic.bucket,
            topic=topic.topic,
            title=f"3 Facts About {topic.topic.title()}",
            description=(
                f"Three fast facts about {topic.topic} for a visual YouTube Short. "
                "Generated locally with Ollama, Piper, whisper.cpp, FFmpeg, and Pexels."
            ),
            hashtags=["#shorts", "#facts", f"#{topic.topic.title().replace(' ', '')}"],
            narration=narration,
            facts=facts,
            subtitle_text=narration,
        )
