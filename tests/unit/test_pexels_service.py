from __future__ import annotations

from pathlib import Path

from youtube_kanaal.config import load_settings
from youtube_kanaal.models.assets import VideoClipAsset
from youtube_kanaal.services.pexels_service import PexelsService


def test_pexels_service_expands_boolean_queries(configured_env) -> None:
    service = PexelsService(load_settings())

    expanded = service._expand_queries('"coral reef" or "reef ecosystem"')

    assert expanded == ["coral reef", "reef ecosystem"]


def test_pexels_service_relevance_bonus_prefers_matching_urls(configured_env) -> None:
    service = PexelsService(load_settings())

    matching = service._relevance_bonus(
        query="saturn rings",
        source_url="https://www.pexels.com/video/saturn-rings-animation-123/",
    )
    unrelated = service._relevance_bonus(
        query="saturn rings",
        source_url="https://www.pexels.com/video/zebra-running-123/",
    )

    assert matching > unrelated


def test_pexels_service_space_bonus_penalizes_unrelated_human_clips(configured_env) -> None:
    service = PexelsService(load_settings())

    astronomy = service._relevance_bonus(
        query="Saturn rings telescope",
        source_url="https://www.pexels.com/video/saturn-rings-astronomy-20713848/",
    )
    unrelated = service._relevance_bonus(
        query="Saturn rings telescope",
        source_url="https://www.pexels.com/video/a-female-model-posing-with-rings-on-her-fingers-9431064/",
    )

    assert astronomy > unrelated


def test_pexels_service_prioritizes_one_clip_per_query_first(configured_env) -> None:
    service = PexelsService(load_settings())
    candidates = [
        VideoClipAsset(
            source_id="a1",
            query="saturn rings",
            source_url="https://example.com/saturn-rings",
            download_url="https://example.com/a1.mp4",
            local_path=Path("a1.mp4"),
            duration_seconds=5,
            width=1080,
            height=1920,
            score=9.5,
        ),
        VideoClipAsset(
            source_id="a2",
            query="saturn rings",
            source_url="https://example.com/saturn-rings-2",
            download_url="https://example.com/a2.mp4",
            local_path=Path("a2.mp4"),
            duration_seconds=5,
            width=1080,
            height=1920,
            score=8.7,
        ),
        VideoClipAsset(
            source_id="b1",
            query="saturn moon orbit",
            source_url="https://example.com/saturn-moon",
            download_url="https://example.com/b1.mp4",
            local_path=Path("b1.mp4"),
            duration_seconds=5,
            width=1080,
            height=1920,
            score=8.0,
        ),
    ]

    prioritized = service._prioritized_candidates(candidates, ["saturn rings", "saturn moon orbit"])

    assert [clip.source_id for clip in prioritized[:2]] == ["a1", "b1"]


def test_download_clip_replaces_corrupt_cached_file(configured_env, monkeypatch) -> None:
    service = PexelsService(load_settings())
    clip_path = configured_env["cache_dir"] / "pexels" / "cached.mp4"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    clip_path.write_bytes(b"")
    clip = VideoClipAsset(
        source_id="cached",
        query="ocean",
        source_url="https://example.com/ocean",
        download_url="https://example.com/cached.mp4",
        local_path=clip_path,
        duration_seconds=5,
        width=1080,
        height=1920,
        score=8.0,
    )

    calls: list[str] = []

    class FakeResponse:
        content = b"valid-video"

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str) -> FakeResponse:
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr(service.client, "get", fake_get)
    monkeypatch.setattr(
        service,
        "_clip_file_is_usable",
        lambda path: path.exists() and path.read_bytes() == b"valid-video",
    )

    service._download_clip(clip)

    assert calls == ["https://example.com/cached.mp4"]
    assert clip_path.read_bytes() == b"valid-video"


def test_download_clip_skips_valid_cached_file(configured_env, monkeypatch) -> None:
    service = PexelsService(load_settings())
    clip_path = configured_env["cache_dir"] / "pexels" / "cached-valid.mp4"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    clip_path.write_bytes(b"valid-video")
    clip = VideoClipAsset(
        source_id="cached-valid",
        query="ocean",
        source_url="https://example.com/ocean",
        download_url="https://example.com/cached-valid.mp4",
        local_path=clip_path,
        duration_seconds=5,
        width=1080,
        height=1920,
        score=8.0,
    )

    def fail_get(_url: str):
        raise AssertionError("download should not run for a valid cached clip")

    monkeypatch.setattr(service.client, "get", fail_get)
    monkeypatch.setattr(
        service,
        "_clip_file_is_usable",
        lambda path: path.exists() and path.read_bytes() == b"valid-video",
    )

    service._download_clip(clip)

    assert clip_path.read_bytes() == b"valid-video"
