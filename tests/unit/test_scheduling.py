from __future__ import annotations

from pathlib import Path

import pytest

from youtube_kanaal.utils.scheduling import (
    build_windows_task_action,
    build_windows_task_name,
    parse_schedule_times,
)


def test_parse_schedule_times_deduplicates_and_preserves_order() -> None:
    parsed = parse_schedule_times("13:00, 18:00 22:00 18:00")

    assert parsed == ["13:00", "18:00", "22:00"]


def test_parse_schedule_times_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        parse_schedule_times("25:00,18:00")


def test_build_windows_task_action_contains_expected_flags(tmp_path: Path) -> None:
    action = build_windows_task_action(
        script_path=tmp_path / "scripts" / "run_scheduled_short.ps1",
        repo_root=tmp_path,
        python_executable=tmp_path / ".venv" / "Scripts" / "python.exe",
        upload=True,
        debug=True,
        privacy_status="private",
    )

    assert "powershell.exe" in action
    assert "-Upload" in action
    assert "-Debug" in action
    assert '-PrivacyStatus "private"' in action


def test_build_windows_task_name_uses_time_suffix() -> None:
    assert build_windows_task_name(prefix="youtube-kanaal-auto-upload", time_value="13:00") == (
        "youtube-kanaal-auto-upload-13-00"
    )
