from __future__ import annotations

from datetime import datetime
from typing import Any

from youtube_kanaal.cli import daily_content


def test_daily_content_can_schedule_youtube_and_publish_reels_immediately(
    configured_env,
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_short_batch(**kwargs):
        captured["short_batch"] = kwargs
        return []

    def fake_long_pipeline(request):
        captured["long_request"] = request
        return object()

    monkeypatch.setattr("youtube_kanaal.cli._run_scheduled_shorts_batch", fake_short_batch)
    monkeypatch.setattr("youtube_kanaal.cli._run_long_pipeline_result", fake_long_pipeline)
    monkeypatch.setattr("youtube_kanaal.cli._render_scheduled_uploads_table", lambda **kwargs: None)
    monkeypatch.setattr("youtube_kanaal.cli._render_long_result", lambda result: None)

    daily_content(
        for_value="2099-04-12",
        short_times="10:00,13:00,15:00,19:00",
        video_time="17:00",
        dry_run=False,
        instagram_reels=True,
        debug=False,
        mock_mode=True,
    )

    short_batch = captured["short_batch"]
    assert short_batch["upload"] is True
    assert short_batch["instagram_upload"] is True
    assert [slot.strftime("%H:%M") for slot in short_batch["publish_slots"]] == [
        "10:00",
        "13:00",
        "15:00",
        "19:00",
    ]
    assert all(isinstance(slot, datetime) for slot in short_batch["publish_slots"])

    long_request = captured["long_request"]
    assert long_request.upload is True
    assert long_request.privacy_status == "private"
    assert long_request.scheduled_publish_at.strftime("%Y-%m-%d %H:%M") == "2099-04-12 17:00"


def test_daily_content_dry_run_disables_instagram_reels(configured_env, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        "youtube_kanaal.cli._run_scheduled_shorts_batch",
        lambda **kwargs: captured.setdefault("short_batch", kwargs) or [],
    )
    monkeypatch.setattr(
        "youtube_kanaal.cli._run_long_pipeline_result",
        lambda request: captured.setdefault("long_request", request) or object(),
    )
    monkeypatch.setattr("youtube_kanaal.cli._render_scheduled_uploads_table", lambda **kwargs: None)
    monkeypatch.setattr("youtube_kanaal.cli._render_long_result", lambda result: None)

    daily_content(
        for_value="2099-04-12",
        short_times="10:00,13:00,15:00,19:00",
        video_time="17:00",
        dry_run=True,
        instagram_reels=True,
        debug=False,
        mock_mode=True,
    )

    assert captured["short_batch"]["upload"] is False
    assert captured["short_batch"]["instagram_upload"] is False
    assert captured["long_request"].upload is False
