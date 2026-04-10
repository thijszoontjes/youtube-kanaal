from __future__ import annotations

import re
import shlex
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


def build_linux_cron_block(
    *,
    marker: str,
    script_path: Path,
    repo_root: Path,
    python_executable: Path,
    times: list[str],
    timezone: str,
    upload: bool,
    debug: bool,
    privacy_status: str | None,
    log_path: Path,
) -> str:
    quoted_repo = shlex.quote(str(repo_root))
    quoted_script = shlex.quote(str(script_path))
    quoted_python = shlex.quote(str(python_executable))
    quoted_log = shlex.quote(str(log_path))

    command = (
        f"cd {quoted_repo} && "
        f"REPO_ROOT={quoted_repo} "
        f"PYTHON_EXE={quoted_python} "
        f"sh {quoted_script}"
    )
    if upload:
        command += " --upload"
    if debug:
        command += " --debug"
    if privacy_status:
        command += f" --privacy-status {shlex.quote(privacy_status)}"
    command += f" >> {quoted_log} 2>&1"

    lines = [f"# >>> {marker} >>>", f"CRON_TZ={timezone}"]
    for time_value in times:
        hour, minute = time_value.split(":")
        lines.append(f"{int(minute)} {int(hour)} * * * {command}")
    lines.append(f"# <<< {marker} <<<")
    return "\n".join(lines) + "\n"
