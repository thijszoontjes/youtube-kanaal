from __future__ import annotations

from datetime import datetime, timezone

from youtube_kanaal.config import load_settings
from youtube_kanaal.db import Database
from youtube_kanaal.models import GeneratedShort, ShortRunRequest, TopicChoice
from youtube_kanaal.pipelines import ShortPipeline, validate_artifact_directory
from youtube_kanaal.utils.similarity import normalize_for_similarity


def test_make_short_pipeline_creates_expected_artifacts(configured_env) -> None:
    settings = load_settings()
    database = Database(settings.database_path)
    database.initialize()
    pipeline = ShortPipeline(settings, database)

    result = pipeline.run(
        ShortRunRequest(
            upload=False,
            debug=False,
            preferred_topic="axolotls",
            preferred_bucket="animals",
            mock_mode=True,
        )
    )

    assert result.output_path.exists()
    assert result.downloads_copy_path is not None
    assert result.downloads_copy_path.exists()
    assert result.metadata_path.exists()

    validation = validate_artifact_directory(result.run_id, settings.output_dir / result.run_id)
    assert validation.valid, validation.errors

    history = database.list_runs(limit=1)
    assert history[0].run_id == result.run_id


class StubDuplicateRecoveryOllamaService:
    def __init__(self) -> None:
        self.topic_calls = 0
        self.content_calls: list[str] = []

    def choose_topic(self, *, excluded_topics: list[str], prompt_path, response_path) -> TopicChoice:
        self.topic_calls += 1
        excluded = {item.lower() for item in excluded_topics}
        if "axolotls" not in excluded:
            return TopicChoice(
                bucket="animals",
                topic="axolotls",
                visual_queries=["axolotls", "axolotls close up"],
                search_terms=["axolotls", "animals"],
            )
        return TopicChoice(
            bucket="animals",
            topic="penguins",
            visual_queries=["penguins", "penguins close up"],
            search_terms=["penguins", "animals"],
        )

    def generate_short_content(self, *, topic: TopicChoice, excluded_titles: list[str], prompt_path, response_path) -> GeneratedShort:
        self.content_calls.append(topic.topic)
        if topic.topic == "axolotls":
            return _build_generated_short(
                topic=topic,
                title="3 Facts About Axolotls",
            )
        return _build_generated_short(
            topic=topic,
            title="3 Wild Facts About Penguins",
        )


def test_pipeline_retries_with_new_topic_when_titles_keep_colliding(configured_env) -> None:
    settings = load_settings(mock_mode=True, retry_attempts=2)
    database = Database(settings.database_path)
    database.initialize()
    database.record_topic(
        topic="wolves",
        bucket="animals",
        title="3 Facts About Axolotls",
        run_id="historic-run",
        created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        normalized_topic=normalize_for_similarity("wolves"),
    )
    ollama = StubDuplicateRecoveryOllamaService()
    pipeline = ShortPipeline(settings, database, ollama_service=ollama)

    result = pipeline.run(
        ShortRunRequest(
            upload=False,
            debug=False,
            mock_mode=True,
        )
    )

    assert result.output_path.exists()
    assert result.topic == "penguins"
    assert ollama.topic_calls >= 2
    assert ollama.content_calls.count("axolotls") == settings.retry_attempts
    assert "penguins" in ollama.content_calls


def _build_generated_short(*, topic: TopicChoice, title: str) -> GeneratedShort:
    narration = (
        f"Here are 3 facts about {topic.topic}. "
        f"First, {topic.topic.title()} appear in places people rarely expect. "
        f"Second, scientists study {topic.topic} because it reveals useful patterns in nature. "
        f"Third, {topic.topic.title()} is visually striking, which makes it perfect for a short explainer. "
        f"That is why {topic.topic} stands out, and why it works so well in a fast visual Short. "
        f"People remember {topic.topic} because it looks so unusual on screen."
    )
    return GeneratedShort(
        bucket=topic.bucket,
        topic=topic.topic,
        title=title,
        description=f"Three fast facts about {topic.topic} for a visual YouTube Short made locally.",
        hashtags=["#shorts", "#facts", f"#{topic.topic.title().replace(' ', '')}"],
        narration=narration,
        facts=[
            f"{topic.topic.title()} appear in places people rarely expect.",
            f"Scientists study {topic.topic} because it reveals useful patterns in nature.",
            f"{topic.topic.title()} is visually striking, which makes it perfect for a short explainer.",
        ],
        subtitle_text=narration,
    )
