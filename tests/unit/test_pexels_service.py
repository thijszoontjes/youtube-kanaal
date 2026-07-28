from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

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


def test_pexels_service_rejects_generic_football_clip_when_named_subjects_do_not_match(configured_env) -> None:
    service = PexelsService(load_settings())

    argentina = service._relevance_bonus(
        query="England Argentina football match",
        source_url="https://www.pexels.com/video/argentina-football-fans-celebrate-123/",
    )
    morocco = service._relevance_bonus(
        query="England Argentina football match",
        source_url="https://www.pexels.com/video/morocco-football-crowd-stadium-456/",
    )

    assert argentina > morocco


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


def test_pexels_service_titanic_queries_penalize_unrelated_historical_sites(configured_env) -> None:
    service = PexelsService(load_settings())

    shipwreck = service._relevance_bonus(
        query="the Titanic shipwreck underwater",
        source_url="https://www.pexels.com/video/ocean-shipwreck-underwater-wreck-123/",
    )
    colosseum = service._relevance_bonus(
        query="the Titanic shipwreck underwater",
        source_url="https://www.pexels.com/video/explore-the-ancient-colosseum-in-rome-36398899/",
    )

    assert shipwreck > colosseum


def test_pexels_service_requires_the_repeated_main_subject(configured_env) -> None:
    service = PexelsService(load_settings())
    queries = [
        "octopus camouflage reveal underwater",
        "octopus skin changing color close up",
        "octopuses underwater",
        "reef macro ocean life",
    ]
    required = service._dominant_subject_tokens(queries)
    actual_octopus = VideoClipAsset(
        source_id="octopus",
        query=queries[0],
        source_url="https://www.pexels.com/video/pulpa-aquarium-biarritz-17836505/",
        download_url="https://example.com/octopus.mp4",
        local_path=Path("octopus.mp4"),
        duration_seconds=17,
        width=1080,
        height=1920,
        score=8.0,
    )
    chemical_liquid = actual_octopus.model_copy(
        update={
            "source_id": "liquid",
            "source_url": "https://www.pexels.com/video/changing-colors-of-chemical-liquid-8325963/",
        }
    )

    assert required == {"octopus"}
    assert service._matches_required_subject(actual_octopus, required)
    assert not service._matches_required_subject(chemical_liquid, required)


def test_pexels_service_rejects_food_context_for_animal_subject(configured_env) -> None:
    service = PexelsService(load_settings())
    cooked_octopus = VideoClipAsset(
        source_id="dish",
        query="octopus pattern change underwater",
        source_url="https://www.pexels.com/video/squeezing-orange-over-grilled-octopus-dish-31998392/",
        download_url="https://example.com/dish.mp4",
        local_path=Path("dish.mp4"),
        duration_seconds=8,
        width=1080,
        height=1920,
        score=8.0,
    )

    assert not service._matches_required_subject(cooked_octopus, {"octopus"})


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


def test_pexels_service_selects_subject_match_below_preferred_score(monkeypatch, configured_env) -> None:
    service = PexelsService(load_settings())
    clip = VideoClipAsset(
        source_id="comet",
        query="comet moving through space",
        source_url="https://www.pexels.com/video/beautiful-comets-in-the-sky-5169262/",
        download_url="https://example.com/comet.mp4",
        local_path=Path("comet.mp4"),
        duration_seconds=8,
        width=1920,
        height=1080,
        score=3.56,
    )
    monkeypatch.setattr(service, "_prepare_clip_for_use", lambda _: True)

    selected = service._select_and_download(
        [clip],
        target_duration_seconds=8,
        queries=["comet moving through space", "comets in space"],
    )

    assert [asset.source_id for asset in selected] == ["comet"]


def test_pexels_service_falls_back_when_result_metadata_omits_subject(monkeypatch, configured_env) -> None:
    service = PexelsService(load_settings())
    clip = VideoClipAsset(
        source_id="solar-system",
        query="comet moving through space",
        source_url="https://www.pexels.com/video/solar-system-animation-14618955/",
        download_url="https://example.com/solar-system.mp4",
        local_path=Path("solar-system.mp4"),
        duration_seconds=8,
        width=1080,
        height=1920,
        score=8.0,
    )
    monkeypatch.setattr(service, "_prepare_clip_for_use", lambda _: True)

    selected = service._select_and_download(
        [clip],
        target_duration_seconds=8,
        queries=["comet moving through space", "comets in space"],
    )

    assert [asset.source_id for asset in selected] == ["solar-system"]


def test_pexels_service_falls_back_after_transient_search_error(monkeypatch, configured_env) -> None:
    service = PexelsService(load_settings(mock_mode=False))
    response_path = configured_env["output_dir"] / "pexels_search.json"
    searched_queries: list[str] = []

    def transient_error(query: str) -> httpx.HTTPStatusError:
        request = httpx.Request(
            "GET",
            f"https://api.pexels.com/videos/search?query={query}&per_page=12&orientation=portrait",
        )
        response = httpx.Response(503, request=request)
        return httpx.HTTPStatusError("503 Service Unavailable", request=request, response=response)

    def fake_search(query: str) -> dict[str, object]:
        searched_queries.append(query)
        if query == "United States vs Bosnia-Herzegovina world round football match":
            raise transient_error(query)
        return {
            "videos": [
                {
                    "id": 123,
                    "duration": 8,
                    "url": "https://www.pexels.com/video/football-match-123/",
                    "video_files": [
                        {
                            "width": 1080,
                            "height": 1920,
                            "link": "https://example.com/football.mp4",
                        }
                    ],
                    "user": {"name": "Pexels Creator"},
                }
            ]
        }

    monkeypatch.setattr(service, "_search", fake_search)
    monkeypatch.setattr(service, "_select_and_download", lambda candidates, *_args, **_kwargs: candidates[:1])

    clips = service.fetch_clips(
        queries=["United States vs Bosnia-Herzegovina world round football match"],
        target_duration_seconds=8,
        response_path=response_path,
    )

    saved_payload = json.loads(response_path.read_text(encoding="utf-8"))
    assert searched_queries[:2] == [
        "United States vs Bosnia-Herzegovina world round football match",
        "football match",
    ]
    assert saved_payload[0]["error"]["status_code"] == 503
    assert saved_payload[1]["fallback"] is True
    assert clips[0].query == "football match"


def test_pexels_service_does_not_fallback_on_auth_error(monkeypatch, configured_env) -> None:
    service = PexelsService(load_settings(mock_mode=False))
    request = httpx.Request("GET", "https://api.pexels.com/videos/search?query=ocean")
    response = httpx.Response(401, request=request)

    def fake_search(query: str) -> dict[str, object]:
        raise httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)

    monkeypatch.setattr(service, "_search", fake_search)

    with pytest.raises(httpx.HTTPStatusError):
        service.fetch_clips(
            queries=["ocean"],
            target_duration_seconds=8,
            response_path=configured_env["output_dir"] / "pexels_search.json",
        )


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
