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


def test_pexels_service_redownloads_invalid_cached_clip(monkeypatch, configured_env) -> None:
    service = PexelsService(load_settings())
    clip_path = service.settings.cache_dir / "pexels" / "broken.mp4"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    clip_path.write_bytes(b"")
    clip = VideoClipAsset(
        source_id="broken",
        query="saturn rings",
        source_url="https://example.com/saturn-rings",
        download_url="https://example.com/broken.mp4",
        local_path=clip_path,
        duration_seconds=5,
        width=1080,
        height=1920,
        score=9.5,
    )

    calls: list[str] = []

    class DummyResponse:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str):
        calls.append(url)
        return DummyResponse(b"0" * 4096)

    validations = iter([False, True])
    monkeypatch.setattr(service.client, "get", fake_get)
    monkeypatch.setattr(service, "_is_valid_clip_file", lambda path: next(validations))

    prepared = service._prepare_clip_for_use(clip)

    assert prepared is True
    assert calls == ["https://example.com/broken.mp4"]
    assert clip.local_path.exists()
    assert clip.local_path.stat().st_size == 4096


def test_pexels_service_skips_clip_when_downloaded_file_is_invalid(monkeypatch, configured_env) -> None:
    service = PexelsService(load_settings())
    clip_path = service.settings.cache_dir / "pexels" / "still-broken.mp4"
    clip = VideoClipAsset(
        source_id="still-broken",
        query="saturn rings",
        source_url="https://example.com/saturn-rings",
        download_url="https://example.com/still-broken.mp4",
        local_path=clip_path,
        duration_seconds=5,
        width=1080,
        height=1920,
        score=9.5,
    )

    class DummyResponse:
        content = b"not-a-real-video"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(service.client, "get", lambda url: DummyResponse())
    monkeypatch.setattr(service, "_is_valid_clip_file", lambda path: False)

    prepared = service._prepare_clip_for_use(clip)

    assert prepared is False
    assert not clip.local_path.exists()
