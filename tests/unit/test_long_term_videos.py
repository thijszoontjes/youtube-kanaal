from __future__ import annotations

import json
from pathlib import Path

import pytest

from youtube_kanaal.cli import app
from youtube_kanaal.models import GeneratedLongVideo, ImageAsset, LongRunRequest, TOPIC_CATALOG, TopicChoice
from youtube_kanaal.prompts import build_long_content_generation_prompt
from youtube_kanaal.services.ollama_service import OllamaService
from youtube_kanaal.services.pexels_service import PexelsService
from youtube_kanaal.services.chatterbox_service import ChatterboxService
from youtube_kanaal.config import load_settings
from youtube_kanaal.db import Database
from youtube_kanaal.pipelines.long_pipeline import LongPipeline


def test_long_run_request_accepts_one_minute_test_and_thumbnail(tmp_path: Path) -> None:
    thumbnail = tmp_path / "thumbnail.png"
    thumbnail.write_bytes(b"thumbnail")

    request = LongRunRequest(test_duration_seconds=60, thumbnail_path=thumbnail)

    assert request.test_duration_seconds == 60
    assert request.thumbnail_path == thumbnail.resolve()


def test_long_prompt_has_separate_one_minute_profile() -> None:
    topic = TopicChoice(
        bucket="human body",
        topic="nutrient deficiencies",
        visual_queries=["nutrient deficiencies", "vitamin deficiency symptoms"],
        search_terms=["nutrient deficiencies"],
    )

    prompt = build_long_content_generation_prompt(topic, [], target_duration_seconds=60)

    assert '"duration_profile": "test"' in prompt
    assert "220-250 words" in prompt
    assert "8:30 to 11:00" in prompt
    assert '"intro"' in prompt


def test_long_prompt_requires_specific_subtopics_and_visual_consistency() -> None:
    topic = TopicChoice(
        bucket="human body",
        topic="nutrient deficiencies",
        visual_queries=["nutrient deficiencies", "vitamin D"],
        search_terms=["nutrient deficiencies", "vitamin D"],
    )

    prompt = build_long_content_generation_prompt(topic, [], target_duration_seconds=60)

    assert "one specific named subtopic" in prompt
    assert "vitamin D" in prompt
    assert "same specific subtopic" in prompt
    assert "separate block in the opening overview tiles" in prompt
    assert "CHEST" in prompt


def test_easiest_muscles_topic_has_concrete_test_blocks(tmp_path: Path) -> None:
    assert "easiest muscles to grow" in TOPIC_CATALOG["human body"]

    service = OllamaService(load_settings(mock_mode=True))
    topic = TopicChoice(
        bucket="human body",
        topic="easiest muscles to grow",
        visual_queries=["easiest muscles to grow", "chest muscle anatomy"],
        search_terms=["easiest muscles to grow", "chest muscle anatomy"],
    )

    content = service._fallback_test_long_content(topic)

    assert len(content.sections) == 6
    assert content.sections[0].title == "Easiest Muscles To Grow Detail 1"
    assert "#EasiestMusclesToGrow" in content.upload_description([(0.0, "CHEST")])


def test_long_sections_preserve_ai_selected_subtopics_and_queries() -> None:
    service = OllamaService(load_settings(mock_mode=True))

    sections = service._fit_long_sections(
        [
            {
                "title": "VITAMIN D",
                "narration": "Vitamin D changes how the body handles calcium. The reason this matters is that the mechanism affects the whole system.",
                "visual_queries": ["vitamin D sunlight", "vitamin D molecule"],
            },
            {
                "title": "ZINC",
                "narration": "Zinc supports several cellular processes. The reason this matters is that those processes affect repair and growth.",
                "visual_queries": ["zinc mineral supplement", "zinc molecule"],
            },
        ],
        "nutrient deficiencies",
        "human body",
        test_mode=True,
    )

    assert sections[0].title == "VITAMIN D"
    assert sections[1].title == "ZINC"
    assert sections[0].visual_queries[:2] == ["vitamin D sunlight", "vitamin D molecule"]


def test_long_title_removes_visual_guide_without_replacing_ai_topic() -> None:
    service = OllamaService(load_settings(mock_mode=True))

    title = service._clean_long_title(
        "A Visual Guide to These Are the Easiest Muscles to Grow",
        "easiest muscles to grow",
    )

    assert "visual guide" not in title.lower()
    assert "easiest muscles to grow" in title.lower()


def test_long_overview_uses_only_one_tile_per_chapter(tmp_path: Path) -> None:
    service = OllamaService(load_settings(mock_mode=True))
    topic = TopicChoice(
        bucket="human body",
        topic="easiest muscles to grow",
        visual_queries=["easiest muscles to grow", "human body"],
        search_terms=["easiest muscles to grow", "human body"],
    )
    content = service._fallback_test_long_content(topic)
    pipeline = LongPipeline(load_settings(mock_mode=True), Database(tmp_path / "database.sqlite"))
    clips = [
        ImageAsset(
            source_id=f"chapter-{index}",
            query=section.visual_queries[0],
            source_url="https://example.invalid/photo",
            download_url="https://example.invalid/photo.jpg",
            local_path=tmp_path / f"chapter-{index}.jpg",
            width=1920,
            height=1080,
        )
        for index, section in enumerate(content.sections)
    ]
    clips.append(
        ImageAsset(
            source_id="extra-search-result",
            query="chest workout gym",
            source_url="https://example.invalid/extra",
            download_url="https://example.invalid/extra.jpg",
            local_path=tmp_path / "extra.jpg",
            width=1920,
            height=1080,
        )
    )

    selected = pipeline._select_long_overview_clips(clips, content)

    assert [clip.source_id for clip in selected] == [f"chapter-{index}" for index in range(6)]


def test_long_chapters_change_visuals_every_few_seconds() -> None:
    durations = LongPipeline._visual_cut_durations(120.0)

    assert len(durations) > 1
    assert all(3.0 <= duration <= 10.0 for duration in durations)
    assert sum(durations) == pytest.approx(120.0)


def test_mock_one_minute_long_content_has_test_profile(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    service = OllamaService(load_settings(mock_mode=True))
    topic = TopicChoice(
        bucket="human body",
        topic="nutrient deficiencies",
        visual_queries=["nutrient deficiencies", "vitamin deficiency symptoms"],
        search_terms=["nutrient deficiencies"],
    )

    content = service.generate_long_content(
        topic=topic,
        excluded_titles=[],
        prompt_path=tmp_path / "prompt.txt",
        response_path=tmp_path / "response.json",
        target_duration_seconds=60,
    )

    assert isinstance(content, GeneratedLongVideo)
    assert content.duration_profile == "test"
    assert 220 <= len(content.narration.split()) <= 250
    assert len(content.sections) == 6


def test_long_narration_removes_search_instructions_and_keeps_visual_queries() -> None:
    service = OllamaService(load_settings(mock_mode=True))

    cleaned = service._clean_narration(
        "Iron deficiency can cause fatigue. Pexels search queries: 'iron deficiency symptoms', 'fatigue causes'."
    )

    assert cleaned == "Iron deficiency can cause fatigue."
    assert service._quoted_visual_queries("Pexels search queries: 'iron deficiency symptoms'") == [
        "iron deficiency symptoms"
    ]


def test_long_narration_removes_duplicate_sentences_and_adds_reason() -> None:
    service = OllamaService(load_settings(mock_mode=True))

    content = service._fit_section_words(
        "Chest responds to pressing. Chest responds to pressing.",
        "easiest muscles to grow",
        1,
        focus="CHEST",
        minimum_words=34,
        maximum_words=38,
        test_mode=True,
    )

    assert content.lower().count("chest responds to pressing") == 1
    assert "the reason this block works is that" in content.lower()
    assert "bigger picture" not in content.lower()
    assert "the reason is direct loading" not in content.lower()


def test_long_narration_adds_why_when_source_has_only_description() -> None:
    service = OllamaService(load_settings(mock_mode=True))

    content = service._fit_section_words(
        "The back muscles support posture and movement.",
        "easiest muscles to grow",
        5,
        focus="BACK",
        minimum_words=34,
        maximum_words=38,
        test_mode=True,
    )

    assert service._contains_reasoning(content)


def test_cli_make_long_test_renders_local_one_minute_package(cli_runner, configured_env) -> None:
    result = cli_runner.invoke(app, ["make-long-test", "--mock-mode"])

    assert result.exit_code == 0, result.stdout
    assert "60.00s" in result.stdout
    run_dir = max(configured_env["output_dir"].iterdir(), key=lambda path: path.stat().st_mtime)
    assert list((run_dir / "video").glob("*.mp4"))
    assert (run_dir / "metadata" / "thumbnail.jpg").exists()
    assert (run_dir / "subtitles" / "long_captions.srt").exists()
    assert (run_dir / "subtitles" / "long_captions.vtt").exists()
    assert (run_dir / "subtitles" / "long_captions.ass").exists()
    assert (run_dir / "video").exists()
    assert (run_dir / "subtitles").exists()
    assert not (run_dir / "assets").exists()
    assert not (run_dir / "audio").exists()
    assert not (run_dir / "prompts").exists()
    assert not (run_dir / "responses").exists()
    cleanup = json.loads((run_dir / "metadata" / "media_cleanup.json").read_text(encoding="utf-8"))
    assert cleanup["cleaned"] is True


def test_mock_long_form_uses_photo_assets(configured_env) -> None:
    service = PexelsService(load_settings(mock_mode=True))

    photos = service.fetch_broll_photos(
        queries=["nutrient deficiencies", "vitamin deficiency symptoms"],
        max_photos=2,
        response_path=configured_env["output_dir"] / "photos.json",
    )

    assert len(photos) == 2
    assert all(photo.local_path.suffix == ".jpg" for photo in photos)


def test_chatterbox_long_form_splits_and_joins_mock_audio(tmp_path: Path) -> None:
    service = ChatterboxService(load_settings(mock_mode=True))
    output_path = tmp_path / "long.wav"
    service.synthesize_long(
        text=" ".join(f"Sentence {index}." for index in range(140)),
        output_path=output_path,
    )

    assert output_path.exists()
    assert service._split_text_for_long_narration("one. two. three.", 2) == ["one. two.", "three."]
