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
    "gaming",
    "sports",
    "vehicles",
    "technology",
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
        "capybaras",
        "arctic foxes",
        "jellyfish",
        "cheetahs",
        "meerkats",
        "sloths",
        "falcons",
        "wolves",
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
        "Jupiter",
        "exoplanets",
        "the Moon",
        "asteroids",
        "rocket launches",
        "Titan",
        "the Sun",
        "nebulae",
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
        "Norway fjords",
        "the Grand Canyon",
        "Mount Everest",
        "Hawaii",
        "the Galapagos Islands",
        "Bali",
        "the Nile River",
        "Yellowstone",
    ],
    "history": [
        "ancient Rome",
        "the Silk Road",
        "the Maya civilization",
        "the Industrial Revolution",
        "the Library of Alexandria",
        "the Great Wall of China",
        "the Renaissance",
        "the Vikings",
        "Pompeii",
        "ancient Egypt",
        "the Aztec Empire",
        "the Inca Empire",
        "Apollo 11",
        "the Titanic",
        "samurai Japan",
        "the Bronze Age",
        "the Ottoman Empire",
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
        "the internet",
        "the barcode",
        "the light bulb",
        "the zipper",
        "the steam engine",
        "the transistor",
        "3D printing",
        "the refrigerator",
        "the telephone",
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
        "dolphins",
        "sharks",
        "sea otters",
        "manta rays",
        "seahorses",
        "icebergs",
        "whale sharks",
        "sea anemones",
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
        "avocados",
        "chili peppers",
        "vanilla",
        "croissants",
        "green tea",
        "bananas",
        "maple syrup",
        "tomatoes",
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
        "the lungs",
        "DNA",
        "adrenaline",
        "digestion",
        "the spine",
        "blood",
        "hormones",
        "the liver",
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
        "monsoons",
        "heat waves",
        "blizzards",
        "sandstorms",
        "waterspouts",
        "ice storms",
        "cyclones",
        "microbursts",
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
        "domes",
        "suspension bridges",
        "Art Deco buildings",
        "ancient temples",
        "medieval fortresses",
        "treehouses",
        "adobe houses",
        "glass towers",
    ],
    "gaming": [
        "Fortnite",
        "Minecraft",
        "Roblox",
        "Tetris",
        "Pac-Man",
        "Super Mario",
        "The Legend of Zelda",
        "Pokemon",
        "Call of Duty",
        "Among Us",
        "Rocket League",
        "League of Legends",
        "Valorant",
        "Grand Theft Auto V",
        "The Sims",
        "Elden Ring",
        "FIFA",
        "Animal Crossing",
    ],
    "sports": [
        "football",
        "basketball",
        "surfing",
        "skateboarding",
        "Formula 1",
        "mountain biking",
        "parkour",
        "tennis",
        "snowboarding",
        "rock climbing",
        "marathon running",
        "swimming",
        "table tennis",
        "gymnastics",
        "boxing",
        "volleyball",
        "skiing",
        "rowing",
    ],
    "vehicles": [
        "supercars",
        "motorcycles",
        "bullet trains",
        "helicopters",
        "fighter jets",
        "sailboats",
        "submarines",
        "monster trucks",
        "cruise ships",
        "cargo ships",
        "classic cars",
        "rescue helicopters",
        "tractors",
        "bicycles",
        "hot air balloons",
        "race boats",
        "scooters",
        "electric trains",
    ],
    "technology": [
        "robots",
        "drones",
        "3D printers",
        "smartphones",
        "AI chips",
        "quantum computers",
        "virtual reality",
        "solar panels",
        "satellites",
        "humanoid robots",
        "smart homes",
        "data centers",
        "wearable tech",
        "computer keyboards",
        "facial recognition",
        "server racks",
        "touchscreens",
        "robot arms",
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
_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_GENERIC_TITLE_HASHTAGS: tuple[str, ...] = (
    "#Shorts",
    "#Facts",
    "#DidYouKnow",
    "#LearnOnYouTube",
    "#InterestingFacts",
)
_BUCKET_HASHTAGS: dict[str, tuple[str, ...]] = {
    "animals": ("#Wildlife", "#AnimalFacts", "#Nature"),
    "space": ("#Space", "#Astronomy", "#SolarSystem"),
    "geography": ("#TravelFacts", "#Geography", "#Earth"),
    "history": ("#History", "#WorldHistory", "#DidYouKnowHistory"),
    "inventions": ("#Innovation", "#Inventions", "#Technology"),
    "ocean": ("#Ocean", "#MarineLife", "#Underwater"),
    "food": ("#FoodFacts", "#Foodie", "#Cooking"),
    "human body": ("#HumanBody", "#Science", "#Biology"),
    "weather": ("#Weather", "#NatureFacts", "#Storms"),
    "architecture": ("#Architecture", "#Design", "#Structures"),
    "gaming": ("#Gaming", "#GameFacts", "#Esports"),
    "sports": ("#Sports", "#AthleteLife", "#Action"),
    "vehicles": ("#Vehicles", "#Transport", "#Motion"),
    "technology": ("#Technology", "#FutureTech", "#Innovation"),
}


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
    hashtags: list[str] = Field(min_length=3, max_length=15)
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
        self.hashtags = self._expand_hashtags(self.hashtags)
        return self

    def estimated_duration_seconds(self) -> float:
        return round(len(self.narration.split()) / 2.6, 2)

    def keyword_queries(self) -> list[str]:
        return [self.topic, f"{self.topic} {self.bucket}", self.bucket]

    def upload_hashtags(self, minimum: int = 10) -> list[str]:
        hashtags = self._expand_hashtags(self.hashtags)
        return hashtags[: max(minimum, len(hashtags))]

    def upload_title(self, *, hashtag_count: int = 3, max_length: int = 100) -> str:
        chosen = self._title_hashtags(count=hashtag_count)
        if not chosen:
            return self.title
        suffix = " ".join(chosen)
        candidate = f"{self.title} {suffix}".strip()
        if len(candidate) <= max_length:
            return candidate
        allowed_title_length = max_length - len(suffix) - 1
        trimmed_title = self.title[:allowed_title_length].rstrip(" -|,")
        return f"{trimmed_title} {suffix}".strip()

    def upload_description(self, minimum_hashtags: int = 10) -> str:
        hashtags = " ".join(self.upload_hashtags(minimum=minimum_hashtags))
        return f"{self.description}\n\n{hashtags}".strip()

    def _expand_hashtags(self, base_hashtags: list[str]) -> list[str]:
        candidates: list[str] = []
        candidates.extend(self._core_hashtag_candidates())
        candidates.extend(base_hashtags)
        candidates.extend(self._title_phrase_hashtag_candidates())
        candidates.extend(_GENERIC_TITLE_HASHTAGS)

        normalized: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            tag = self._normalize_hashtag(candidate)
            if not tag:
                continue
            key = tag.lower()
            if key in seen:
                continue
            normalized.append(tag)
            seen.add(key)
        return normalized[:15]

    def _title_hashtags(self, *, count: int) -> list[str]:
        generic = {tag.lower() for tag in _GENERIC_TITLE_HASHTAGS}
        preferred = []
        preferred.extend(self._core_hashtag_candidates())
        preferred.extend(self.hashtags)
        preferred.extend(self.upload_hashtags())

        chosen: list[str] = []
        seen: set[str] = set()
        for candidate in preferred:
            tag = self._normalize_hashtag(candidate)
            if not tag or tag.lower() in generic:
                continue
            if tag.lower() in seen:
                continue
            chosen.append(tag)
            seen.add(tag.lower())
        return chosen[:count]

    def _core_hashtag_candidates(self) -> list[str]:
        candidates: list[str] = []
        topic_tag = self._normalize_hashtag(self.topic)
        if topic_tag:
            candidates.append(topic_tag)
        candidates.extend(_BUCKET_HASHTAGS.get(self.bucket, ()))
        return candidates

    def _title_phrase_hashtag_candidates(self) -> list[str]:
        topic_tag = self._normalize_hashtag(self.topic)
        title_tokens = [token for token in _WORD_RE.findall(self.title) if len(token) > 2]
        topic_tokens = [token for token in _WORD_RE.findall(self.topic) if len(token) > 2]
        fact_tokens = [token for token in _WORD_RE.findall(" ".join(self.facts)) if len(token) > 4]

        phrases: list[str] = []
        if topic_tag:
            phrases.append(topic_tag)
        if topic_tokens:
            phrases.append("#" + "".join(word.capitalize() for word in topic_tokens))
        if title_tokens:
            phrases.append("#" + "".join(word.capitalize() for word in title_tokens[:3]))
        if len(title_tokens) >= 4:
            phrases.append("#" + "".join(word.capitalize() for word in title_tokens[1:4]))
        if fact_tokens:
            phrases.append("#" + "".join(word.capitalize() for word in fact_tokens[:2]))
        return phrases

    def _normalize_hashtag(self, raw_value: str) -> str | None:
        words = _WORD_RE.findall(raw_value)
        if not words:
            return None
        return "#" + "".join(word.capitalize() for word in words)
