from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest

from youtube_kanaal.config import load_settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.services.world_cup_service import WorldCupService


def _scoreboard_payload(*, event_id: str = "760421", date_value: str = "2026-06-14T04:00Z") -> dict[str, object]:
    return {
        "events": [
            {
                "id": event_id,
                "date": date_value,
                "competitions": [
                    {
                        "attendance": 52497,
                        "altGameNote": "FIFA World Cup, Group D",
                        "status": {"type": {"state": "post", "completed": True, "detail": "FT"}},
                        "venue": {
                            "fullName": "BC Place",
                            "address": {"city": "Vancouver", "country": "Canada"},
                        },
                        "competitors": [
                            {
                                "homeAway": "home",
                                "winner": True,
                                "score": "2",
                                "team": {"displayName": "Australia"},
                                "statistics": [{"name": "possessionPct", "displayValue": "28.4"}],
                            },
                            {
                                "homeAway": "away",
                                "winner": False,
                                "score": "1",
                                "team": {"displayName": "Türkiye"},
                                "statistics": [{"name": "possessionPct", "displayValue": "71.6"}],
                            },
                        ],
                    }
                ],
            }
        ]
    }


def test_world_cup_service_builds_grounded_completed_match_short(configured_env, tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_scoreboard_payload(), request=request)

    service = WorldCupService(
        load_settings(world_cup_past_days=0, world_cup_future_days=0),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    event = service.fetch_relevant_event(
        excluded_topics=[],
        response_path=tmp_path / "scoreboard.json",
        now=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
    )
    content = service.build_short(event)
    topic = service.topic_choice(event)

    assert topic.bucket == "world cup 2026"
    assert topic.topic == "Australia vs Türkiye"
    assert "Australia beat Türkiye 2-1" in content.narration
    assert "Possession was 28.4 percent for Australia" in content.narration
    assert "BC Place, Vancouver, Canada" in content.narration
    assert "gameId/760421" in content.description
    assert len(content.facts) == 3


def test_world_cup_service_skips_recent_match_topics(configured_env, tmp_path) -> None:
    payload = _scoreboard_payload()
    second = _scoreboard_payload(event_id="760422", date_value="2026-06-13T04:00Z")["events"][0]
    second["competitions"][0]["competitors"][0]["team"]["displayName"] = "Brazil"
    second["competitions"][0]["competitors"][1]["team"]["displayName"] = "Morocco"
    payload["events"].append(second)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    service = WorldCupService(
        load_settings(world_cup_past_days=0, world_cup_future_days=0),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    event = service.fetch_relevant_event(
        excluded_topics=["Australia vs Türkiye"],
        response_path=tmp_path / "scoreboard.json",
        now=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
    )

    assert event.topic == "Brazil vs Morocco"


def test_world_cup_service_puts_winner_score_first_for_away_win(configured_env) -> None:
    service = WorldCupService(load_settings())
    event = service._parse_events(_scoreboard_payload())[0]
    away_win = replace(
        event,
        home=replace(event.home, score=0, winner=False),
        away=replace(event.away, score=3, winner=True),
    )

    content = service.build_short(away_win)

    assert "Türkiye beat Australia 3-0" in content.narration
    assert "Australia beat Türkiye 0-3" not in content.narration


def test_world_cup_service_varies_completed_match_angles(configured_env) -> None:
    service = WorldCupService(load_settings())
    base = service._parse_events(_scoreboard_payload())[0]
    blowout = replace(
        base,
        event_id="760422",
        home=replace(base.home, score=7, possession="64.6"),
        away=replace(base.away, score=1, possession="35.4"),
    )
    high_scoring_draw = replace(
        base,
        event_id="760425",
        home=replace(base.home, score=2, possession="59.8"),
        away=replace(base.away, score=2, possession="40.2"),
    )

    packages = [service.build_short(event) for event in (base, blowout, high_scoring_draw)]

    assert len({package.title for package in packages}) == 3
    assert len({package.narration.split(".")[0] for package in packages}) == 3
    assert any("less of the ball" in package.narration or "Possession told" in package.narration for package in packages)
    assert any("winning margin" in package.narration or "The gap was" in package.narration for package in packages)
    assert any("combined for 4 goals" in package.narration for package in packages)


def test_world_cup_service_fails_closed_when_source_is_unavailable(configured_env, tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    service = WorldCupService(
        load_settings(world_cup_past_days=0, world_cup_future_days=0),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(PipelineStageError, match="trustworthy current World Cup data"):
        service.fetch_relevant_event(
            excluded_topics=[],
            response_path=tmp_path / "scoreboard.json",
            now=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
        )
