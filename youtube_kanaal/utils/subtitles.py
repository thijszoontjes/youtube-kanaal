from __future__ import annotations

from math import ceil


def _format_timestamp(seconds: float, *, vtt: bool = False) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    milliseconds = int(round((seconds - int(seconds)) * 1000))
    separator = "." if vtt else ","
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{separator}{milliseconds:03d}"


def build_timed_subtitles(lines: list[str], total_duration_seconds: float) -> str:
    chunk_duration = max(total_duration_seconds / max(len(lines), 1), 1.0)
    entries: list[str] = []
    current = 0.0
    for index, line in enumerate(lines, start=1):
        start = current
        end = min(total_duration_seconds, start + chunk_duration)
        entries.append(
            "\n".join(
                [
                    str(index),
                    f"{_format_timestamp(start)} --> {_format_timestamp(end)}",
                    line.strip(),
                ]
            )
        )
        current = end
    return "\n\n".join(entries) + "\n"


def build_vtt_from_srt_text(srt_text: str) -> str:
    lines = ["WEBVTT", ""]
    for raw_line in srt_text.strip().splitlines():
        if raw_line.isdigit():
            continue
        line = raw_line.replace(",", ".") if "-->" in raw_line else raw_line
        lines.append(line)
    return "\n".join(lines) + "\n"


def split_subtitle_lines(text: str, max_words_per_line: int = 8) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    for index in range(0, len(words), max_words_per_line):
        chunks.append(" ".join(words[index : index + max_words_per_line]))
    return chunks or [text]


def estimate_runtime_from_text(text: str, words_per_second: float = 2.6) -> float:
    return round(max(len(text.split()) / words_per_second, 1.0), 2)


def ideal_clip_count(total_duration_seconds: float) -> int:
    return max(3, ceil(total_duration_seconds / 8))
