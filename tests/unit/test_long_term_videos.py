from __future__ import annotations

from pathlib import Path

from youtube_kanaal.cli import app
from youtube_kanaal.models import GeneratedLongVideo, LongRunRequest, TopicChoice
from youtube_kanaal.prompts import build_long_content_generation_prompt
from youtube_kanaal.services.ollama_service import OllamaService
from youtube_kanaal.services.pexels_service import PexelsService
from youtube_kanaal.services.chatterbox_service import ChatterboxService
from youtube_kanaal.config import load_settings


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
