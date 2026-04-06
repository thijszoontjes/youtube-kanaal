from __future__ import annotations

import re
from pathlib import Path


_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def parse_schedule_times(raw_times: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if raw_times is None:
        return []

    if isinstance(raw_times, str):
        candidates = re.split(r"[\s,;]+", raw_times.strip())
    else:
        candidates = [str(item).strip() for item in raw_times]

    parsed: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        if not _TIME_RE.fullmatch(candidate):
            raise ValueError(f"Invalid schedule time: {candidate}. Use HH:MM in 24-hour format.")
        if candidate not in parsed:
            parsed.append(candidate)
    return parsed


def build_windows_task_name(*, prefix: str, time_value: str) -> str:
    return f"{prefix}-{time_value.replace(':', '-')}"


def build_windows_task_action(
    *,
    script_path: Path,
    repo_root: Path,
    python_executable: Path,
    upload: bool,
    debug: bool,
    privacy_status: str | None,
) -> str:
    command_parts = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        f'"{script_path}"',
        "-RepoRoot",
        f'"{repo_root}"',
        "-PythonExe",
        f'"{python_executable}"',
    ]
    if upload:
        command_parts.append("-Upload")
    if debug:
        command_parts.append("-Debug")
    if privacy_status:
        command_parts.extend(["-PrivacyStatus", f'"{privacy_status}"'])
    return " ".join(command_parts)
