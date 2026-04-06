from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator, model_validator


ALLOWED_BUCKETS: tuple[str, ...] = (
    "animals",
    "space",
    "geography",
    "history",
    "inventions",
    "ocean",
    "food",
    "human body",
    "weather",
    "architecture",
)

TOPIC_CATALOG: dict[str, list[str]] = {
    "animals": [
        "axolotls",
        "mantis shrimp",
        "snow leopards",
        "honey badgers",
        "octopuses",
        "hummingbirds",
        "penguins",
        "red pandas",
        "orca whales",
        "leafcutter ants",
    ],
    "space": [
        "Saturn",
        "black holes",
        "comets",
        "neutron stars",
        "Mars",
        "Europa",
        "the Milky Way",
        "solar eclipses",
        "the International Space Station",
        "Venus",
    ],
    "geography": [
        "Iceland",
        "the Sahara Desert",
        "the Amazon River",
        "New Zealand",
        "the Alps",
        "Antarctica",
        "Japan",
        "Patagonia",
        "the Maldives",
        "Greenland",
    ],
    "history": [
        "ancient Rome",
        "the Silk Road",
        "the printing press",
        "the Maya civilization",
        "the Industrial Revolution",
        "the Library of Alexandria",
        "the Great Wall of China",
        "the Renaissance",
        "the Vikings",
        "Pompeii",
    ],
    "inventions": [
        "the compass",
        "the telescope",
        "the microwave oven",
        "Velcro",
        "the airplane",
        "the camera",
        "GPS",
        "the battery",
        "the printing press",
        "the internet",
    ],
    "ocean": [
        "coral reefs",
        "giant kelp forests",
        "deep sea vents",
        "mangroves",
        "blue whales",
        "sea turtles",
        "bioluminescent plankton",
        "tsunamis",
        "tides",
        "the Mariana Trench",
    ],
    "food": [
        "chocolate",
        "sushi",
        "coffee",
        "honey",
        "cheese",
        "olive oil",
        "pineapples",
        "pasta",
        "cinnamon",
        "potatoes",
    ],
    "human body": [
        "the human brain",
        "your skin",
        "the immune system",
        "bones",
        "sleep",
        "muscles",
        "the heart",
        "your eyes",
        "taste buds",
        "memory",
    ],
    "weather": [
        "lightning",
        "tornadoes",
        "rainbows",
        "hurricanes",
        "snowflakes",
        "auroras",
        "thunderstorms",
        "clouds",
        "hail",
        "fog",
    ],
    "architecture": [
        "skyscrapers",
        "bridges",
        "castles",
        "pagodas",
        "Roman aqueducts",
        "windmills",
        "cathedrals",
        "the Colosseum",
        "traditional Japanese houses",
        "lighthouses",
    ],
}

_BANNED_PHRASES: tuple[str, ...] = (
    "some people say",
    "nobody knows for sure",
    "might be true",
    "i am not sure",
    "i'm not sure",
    "it is believed",
    "controversial",
    "medical advice",
)
_EMOJI_RE = re.compile(r"[\U00010000-\U0010FFFF]")
_WHITESPACE_RE = re.compile(r"\s+")


class TopicChoice(BaseModel):
    bucket: str
    topic: str = Field(min_length=3, max_length=80)
    visual_queries: list[str] = Field(min_length=2, max_length=5)
    search_terms: list[str] = Field(default_factory=list, min_length=1, max_length=6)

    @field_validator("bucket")
    @classmethod
    def _validate_bucket(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_BUCKETS:
            raise ValueError(f"Bucket must be one of {', '.join(ALLOWED_BUCKETS)}.")
        return normalized

    @field_validator("topic")
    @classmethod
    def _clean_topic(cls, value: str) -> str:
        cleaned = _WHITESPACE_RE.sub(" ", value.strip())
        if _EMOJI_RE.search(cleaned):
            raise ValueError("Topic must not contain emoji.")
        return cleaned

    @field_validator("visual_queries", "search_terms")
    @classmethod
    def _normalize_list(cls, values: list[str]) -> list[str]:
        cleaned = [_WHITESPACE_RE.sub(" ", item.strip()) for item in values if item.strip()]
        deduped: list[str] = []
        seen: set[str] = set()
        for item in cleaned:
            key = item.lower()
            if key not in seen:
                deduped.append(item)
                seen.add(key)
        if not deduped:
            raise ValueError("At least one search term is required.")
        return deduped

    @model_validator(mode="after")
    def _ensure_topic_matches_bucket(self) -> "TopicChoice":
        if self.topic.lower() not in {topic.lower() for topic in TOPIC_CATALOG.get(self.bucket, [])}:
            raise ValueError("Topic must come from the curated catalog for its bucket.")
        if self.topic not in self.search_terms:
            self.search_terms.insert(0, self.topic)
        return self


class GeneratedShort(BaseModel):
    bucket: str
    topic: str
    title: str = Field(min_length=15, max_length=80)
    description: str = Field(min_length=40, max_length=500)
    hashtags: list[str] = Field(min_length=3, max_length=8)
    narration: str = Field(min_length=80, max_length=700)
    facts: list[str] = Field(min_length=3, max_length=3)
    subtitle_text: str = Field(min_length=20, max_length=700)

    banned_phrases: ClassVar[tuple[str, ...]] = _BANNED_PHRASES

    @field_validator("bucket")
    @classmethod
    def _validate_bucket(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_BUCKETS:
            raise ValueError(f"Bucket must be one of {', '.join(ALLOWED_BUCKETS)}.")
        return normalized

    @field_validator("topic", "title", "description", "narration", "subtitle_text")
    @classmethod
    def _validate_text_fields(cls, value: str) -> str:
        cleaned = _WHITESPACE_RE.sub(" ", value.strip())
        lowered = cleaned.lower()
        if _EMOJI_RE.search(cleaned):
            raise ValueError("Emoji are not allowed.")
        if any(phrase in lowered for phrase in _BANNED_PHRASES):
            raise ValueError("Banned uncertainty or unsafe phrase detected.")
        return cleaned

    @field_validator("hashtags")
    @classmethod
    def _validate_hashtags(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            tag = item.strip()
            if not tag.startswith("#"):
                tag = f"#{tag.lstrip('#')}"
            if " " in tag:
                raise ValueError("Hashtags must not contain spaces.")
            cleaned.append(tag)
        if len({tag.lower() for tag in cleaned}) != len(cleaned):
            raise ValueError("Hashtags must be unique.")
        return cleaned

    @field_validator("facts")
    @classmethod
    def _validate_facts(cls, value: list[str]) -> list[str]:
        if len(value) != 3:
            raise ValueError("Exactly three facts are required.")
        cleaned = [_WHITESPACE_RE.sub(" ", item.strip()) for item in value]
        lowered = [item.lower() for item in cleaned]
        if len(set(lowered)) != 3:
            raise ValueError("Facts must be distinct.")
        for item in lowered:
            if any(phrase in item for phrase in _BANNED_PHRASES):
                raise ValueError("Facts contain a banned phrase.")
            if _EMOJI_RE.search(item):
                raise ValueError("Emoji are not allowed in facts.")
        return cleaned

    @model_validator(mode="after")
    def _validate_duration(self) -> "GeneratedShort":
        word_count = len(self.narration.split())
        if not 45 <= word_count <= 90:
            raise ValueError("Narration should be roughly 20-35 seconds of speech.")
        if len(self.title) > 70:
            raise ValueError("Title is too long for a Short.")
        return self

    def estimated_duration_seconds(self) -> float:
        return round(len(self.narration.split()) / 2.6, 2)

    def keyword_queries(self) -> list[str]:
        return [self.topic, f"{self.topic} {self.bucket}", self.bucket]
