from __future__ import annotations

import re
from dataclasses import dataclass
from math import ceil


@dataclass
class SubtitleCue:
    start_seconds: float
    end_seconds: float
    text: str


_SRT_BLOCK_SPLIT_RE = re.compile(r"\r?\n\r?\n+")


def _format_timestamp(seconds: float, *, vtt: bool = False) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    milliseconds = int(round((seconds - int(seconds)) * 1000))
    separator = "." if vtt else ","
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{separator}{milliseconds:03d}"


def _parse_timestamp(raw_value: str) -> float:
    hours, minutes, seconds = raw_value.replace(",", ".").split(":")
    return (int(hours) * 3600) + (int(minutes) * 60) + float(seconds)


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


def build_srt_from_cues(cues: list[SubtitleCue]) -> str:
    entries: list[str] = []
    for index, cue in enumerate(cues, start=1):
        entries.append(
            "\n".join(
                [
                    str(index),
                    f"{_format_timestamp(cue.start_seconds)} --> {_format_timestamp(cue.end_seconds)}",
                    cue.text.strip(),
                ]
            )
        )
    return "\n\n".join(entries).strip() + "\n"


def build_vtt_from_srt_text(srt_text: str) -> str:
    lines = ["WEBVTT", ""]
    for raw_line in srt_text.strip().splitlines():
        if raw_line.isdigit():
            continue
        line = raw_line.replace(",", ".") if "-->" in raw_line else raw_line
        lines.append(line)
    return "\n".join(lines) + "\n"


def split_subtitle_lines(text: str, max_words_per_line: int = 4) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    for index in range(0, len(words), max_words_per_line):
        chunks.append(" ".join(words[index : index + max_words_per_line]))
    return chunks or [text]


def estimate_runtime_from_text(text: str, words_per_second: float = 2.6) -> float:
    return round(max(len(text.split()) / words_per_second, 1.0), 2)


def ideal_clip_count(total_duration_seconds: float) -> int:
    return max(4, ceil(total_duration_seconds / 5.5))


def parse_srt_text(srt_text: str) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    for block in _SRT_BLOCK_SPLIT_RE.split(srt_text.strip()):
        if not block.strip():
            continue
        raw_lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not raw_lines:
            continue
        if raw_lines[0].isdigit():
            raw_lines = raw_lines[1:]
        if not raw_lines or "-->" not in raw_lines[0]:
            continue
        start_raw, end_raw = [item.strip() for item in raw_lines[0].split("-->", maxsplit=1)]
        text = " ".join(line.strip() for line in raw_lines[1:]).strip()
        if not text:
            continue
        cues.append(
            SubtitleCue(
                start_seconds=_parse_timestamp(start_raw),
                end_seconds=_parse_timestamp(end_raw),
                text=text,
            )
        )
    return cues


def normalize_whisper_srt(
    srt_text: str,
    *,
    max_words_per_cue: int = 4,
    max_chars_per_line: int = 18,
    min_cue_duration_seconds: float = 0.45,
) -> str:
    normalized_cues: list[SubtitleCue] = []
    for cue in parse_srt_text(srt_text):
        chunked_words = _chunk_words(
            cue.text.split(),
            max_words_per_cue=max_words_per_cue,
            max_chars_per_line=max_chars_per_line,
        )
        if not chunked_words:
            continue
        total_duration = max(cue.end_seconds - cue.start_seconds, min_cue_duration_seconds)
        if len(chunked_words) == 1:
            normalized_cues.append(
                SubtitleCue(
                    start_seconds=cue.start_seconds,
                    end_seconds=cue.end_seconds,
                    text="\n".join(_wrap_words(chunked_words[0], max_chars_per_line=max_chars_per_line)),
                )
            )
            continue

        word_weights = [max(len(words), 1) for words in chunked_words]
        total_weight = sum(word_weights)
        cursor = cue.start_seconds
        for index, words in enumerate(chunked_words):
            if index == len(chunked_words) - 1:
                next_cursor = cue.end_seconds
            else:
                remaining_chunks = len(chunked_words) - index - 1
                proportional_duration = total_duration * (word_weights[index] / total_weight)
                reserved_tail = remaining_chunks * min_cue_duration_seconds
                next_cursor = min(
                    cue.end_seconds - reserved_tail,
                    cursor + max(min_cue_duration_seconds, proportional_duration),
                )
            if next_cursor <= cursor:
                next_cursor = min(cue.end_seconds, cursor + min_cue_duration_seconds)
            normalized_cues.append(
                SubtitleCue(
                    start_seconds=cursor,
                    end_seconds=next_cursor,
                    text="\n".join(_wrap_words(words, max_chars_per_line=max_chars_per_line)),
                )
            )
            cursor = next_cursor
    merged_cues = _merge_brief_cues(
        normalized_cues,
        max_words_per_cue=max_words_per_cue,
        max_chars_per_line=max_chars_per_line,
    )
    return build_srt_from_cues(merged_cues)


def align_script_to_reference_srt(
    reference_srt_text: str,
    script_text: str,
    *,
    max_words_per_cue: int = 4,
    max_chars_per_line: int = 18,
    min_cue_duration_seconds: float = 0.45,
) -> str:
    reference_cues = parse_srt_text(reference_srt_text)
    script_words = script_text.split()
    if not reference_cues or not script_words:
        return normalize_whisper_srt(
            build_timed_subtitles(split_subtitle_lines(script_text, max_words_per_line=max_words_per_cue), 1.0),
            max_words_per_cue=max_words_per_cue,
            max_chars_per_line=max_chars_per_line,
            min_cue_duration_seconds=min_cue_duration_seconds,
        )

    total_duration = sum(max(cue.end_seconds - cue.start_seconds, min_cue_duration_seconds) for cue in reference_cues)
    remaining_words = len(script_words)
    remaining_cues = len(reference_cues)
    cursor = 0
    provisional_cues: list[SubtitleCue] = []

    for cue in reference_cues:
        cue_duration = max(cue.end_seconds - cue.start_seconds, min_cue_duration_seconds)
        if remaining_cues == 1:
            take = remaining_words
        else:
            proportional_take = round(len(script_words) * (cue_duration / total_duration))
            take = max(1, proportional_take)
            take = min(take, remaining_words - (remaining_cues - 1))
        chunk_text = " ".join(script_words[cursor : cursor + take]).strip()
        provisional_cues.append(
            SubtitleCue(
                start_seconds=cue.start_seconds,
                end_seconds=cue.end_seconds,
                text=chunk_text,
            )
        )
        cursor += take
        remaining_words -= take
        remaining_cues -= 1

    return normalize_whisper_srt(
        build_srt_from_cues(provisional_cues),
        max_words_per_cue=max_words_per_cue,
        max_chars_per_line=max_chars_per_line,
        min_cue_duration_seconds=min_cue_duration_seconds,
    )


def _chunk_words(
    words: list[str],
    *,
    max_words_per_cue: int,
    max_chars_per_line: int,
) -> list[list[str]]:
    if not words:
        return []
    chunks: list[list[str]] = []
    current: list[str] = []
    max_chunk_chars = max_chars_per_line * 2 + 2
    for word in words:
        candidate = current + [word]
        candidate_text = " ".join(candidate)
        if current and (len(candidate) > max_words_per_cue or len(candidate_text) > max_chunk_chars):
            chunks.append(current)
            current = [word]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _wrap_words(words: list[str], *, max_chars_per_line: int) -> list[str]:
    text = " ".join(words).strip()
    if len(text) <= max_chars_per_line or len(words) <= 2:
        return [text]

    best_split_index = 1
    best_score = float("inf")
    for index in range(1, len(words)):
        left = " ".join(words[:index]).strip()
        right = " ".join(words[index:]).strip()
        longest = max(len(left), len(right))
        line_delta = abs(len(left) - len(right))
        penalty = 0 if longest <= max_chars_per_line else (longest - max_chars_per_line) * 10
        score = longest + line_delta + penalty
        if score < best_score:
            best_score = score
            best_split_index = index
    return [
        " ".join(words[:best_split_index]).strip(),
        " ".join(words[best_split_index:]).strip(),
    ]


def _merge_brief_cues(
    cues: list[SubtitleCue],
    *,
    max_words_per_cue: int,
    max_chars_per_line: int,
) -> list[SubtitleCue]:
    merged: list[SubtitleCue] = []
    index = 0
    while index < len(cues):
        current_words = cues[index].text.replace("\n", " ").split()
        if len(current_words) == 1 and merged:
            previous_words = merged[-1].text.replace("\n", " ").split()
            combined_words = previous_words + current_words
            if len(combined_words) <= max_words_per_cue + 1:
                merged[-1] = SubtitleCue(
                    start_seconds=merged[-1].start_seconds,
                    end_seconds=cues[index].end_seconds,
                    text="\n".join(_wrap_words(combined_words, max_chars_per_line=max_chars_per_line)),
                )
                index += 1
                continue
        if len(current_words) == 1 and index + 1 < len(cues):
            next_words = cues[index + 1].text.replace("\n", " ").split()
            combined_words = current_words + next_words
            if len(combined_words) <= max_words_per_cue + 1:
                merged.append(
                    SubtitleCue(
                        start_seconds=cues[index].start_seconds,
                        end_seconds=cues[index + 1].end_seconds,
                        text="\n".join(_wrap_words(combined_words, max_chars_per_line=max_chars_per_line)),
                    )
                )
                index += 2
                continue
        merged.append(
            SubtitleCue(
                start_seconds=cues[index].start_seconds,
                end_seconds=cues[index].end_seconds,
                text="\n".join(_wrap_words(current_words, max_chars_per_line=max_chars_per_line)),
            )
        )
        index += 1
    return merged
