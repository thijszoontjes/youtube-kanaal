from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from math import ceil


@dataclass
class SubtitleCue:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass
class TimedWord:
    text: str
    start_seconds: float
    end_seconds: float


_SRT_BLOCK_SPLIT_RE = re.compile(r"\r?\n\r?\n+")
_ALIGNMENT_TOKEN_CLEAN_RE = re.compile(r"(^[^\w]+|[^\w]+$)")
_ALIGNMENT_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_SENTENCE_END_RE = re.compile(r"[.!?]+[\"')\]]*$")
_NUMBER_WORDS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
    "10": "ten",
    "11": "eleven",
    "12": "twelve",
    "13": "thirteen",
    "14": "fourteen",
    "15": "fifteen",
    "16": "sixteen",
    "17": "seventeen",
    "18": "eighteen",
    "19": "nineteen",
    "20": "twenty",
}


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


def build_ass_from_srt_text(
    srt_text: str,
    *,
    font_name: str,
    font_size: int,
    margin_v: int,
    outline: int,
    primary_color: str,
    highlight_color: str,
    outline_color: str,
    back_color: str,
    margin_l: int = 96,
    margin_r: int = 96,
    alignment: int = 2,
    style_name: str = "Shorts",
) -> str:
    cues = parse_srt_text(srt_text)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        (
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
            "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
            "Alignment,MarginL,MarginR,MarginV,Encoding"
        ),
        (
            f"Style: {style_name},{font_name},{font_size},{primary_color},{highlight_color},"
            f"{outline_color},{back_color},-1,0,0,0,100,100,0,0,1,{outline},0,"
            f"{alignment},{margin_l},{margin_r},{margin_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]

    for cue in cues:
        lines.extend(
            _build_ass_events_for_cue(
                cue,
                style_name=style_name,
                primary_color=primary_color,
                highlight_color=highlight_color,
                y_position=max(240, 1920 - margin_v),
            )
        )
    return "\n".join(lines).strip() + "\n"


def split_subtitle_lines(text: str, max_words_per_line: int = 3) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    for index in range(0, len(words), max_words_per_line):
        chunks.append(" ".join(words[index : index + max_words_per_line]))
    return chunks or [text]


def estimate_runtime_from_text(text: str, words_per_second: float = 2.6) -> float:
    return round(max(len(text.split()) / words_per_second, 1.0), 2)


def ideal_clip_count(total_duration_seconds: float) -> int:
    return max(5, ceil(total_duration_seconds / 4.5))


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
    max_words_per_cue: int = 3,
    max_chars_per_line: int = 16,
    min_cue_duration_seconds: float = 0.45,
) -> str:
    timed_words = _extract_timed_words(parse_srt_text(srt_text), min_word_duration_seconds=min_cue_duration_seconds / 2)
    normalized_cues = _build_cues_from_timed_words(
        timed_words,
        max_words_per_cue=max_words_per_cue,
        max_chars_per_line=max_chars_per_line,
        min_cue_duration_seconds=min_cue_duration_seconds,
    )
    return build_srt_from_cues(normalized_cues)


def align_script_to_reference_srt(
    reference_srt_text: str,
    script_text: str,
    *,
    max_words_per_cue: int = 3,
    max_chars_per_line: int = 16,
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
    reference_words = _extract_timed_words(reference_cues, min_word_duration_seconds=min_cue_duration_seconds / 2)
    aligned_words = _align_script_words_to_reference(
        reference_words,
        script_words,
        min_word_duration_seconds=min_cue_duration_seconds / 2,
    )
    aligned_cues = _build_cues_from_timed_words(
        aligned_words,
        max_words_per_cue=max_words_per_cue,
        max_chars_per_line=max_chars_per_line,
        min_cue_duration_seconds=min_cue_duration_seconds,
    )
    return build_srt_from_cues(aligned_cues)


def _extract_timed_words(
    cues: list[SubtitleCue],
    *,
    min_word_duration_seconds: float,
) -> list[TimedWord]:
    timed_words: list[TimedWord] = []
    for cue in cues:
        words = cue.text.split()
        if not words:
            continue
        total_duration = max(cue.end_seconds - cue.start_seconds, min_word_duration_seconds)
        weights = [max(len(_normalize_alignment_token(word)), 1) for word in words]
        total_weight = sum(weights)
        cursor = cue.start_seconds
        for index, word in enumerate(words):
            if index == len(words) - 1:
                next_cursor = cue.end_seconds
            else:
                proportional_duration = total_duration * (weights[index] / total_weight)
                next_cursor = min(cue.end_seconds, cursor + max(proportional_duration, min_word_duration_seconds))
            if next_cursor <= cursor:
                next_cursor = min(cue.end_seconds, cursor + min_word_duration_seconds)
            timed_words.append(
                TimedWord(
                    text=word,
                    start_seconds=cursor,
                    end_seconds=next_cursor,
                )
            )
            cursor = next_cursor
    return timed_words


def _build_cues_from_timed_words(
    timed_words: list[TimedWord],
    *,
    max_words_per_cue: int,
    max_chars_per_line: int,
    min_cue_duration_seconds: float,
) -> list[SubtitleCue]:
    provisional: list[list[TimedWord]] = []
    current: list[TimedWord] = []
    max_chunk_chars = max_chars_per_line * 2 + 2

    for word in timed_words:
        if not current:
            current = [word]
            continue

        candidate_words = [item.text for item in current] + [word.text]
        candidate_text = " ".join(candidate_words)
        gap_seconds = max(word.start_seconds - current[-1].end_seconds, 0.0)
        should_break = (
            _should_force_break_after(current[-1].text)
            or gap_seconds > max(min_cue_duration_seconds * 0.55, 0.32)
            or len(candidate_words) > max_words_per_cue
            or len(candidate_text) > max_chunk_chars
        )
        if should_break:
            provisional.append(current)
            current = [word]
            continue
        current.append(word)

    if current:
        provisional.append(current)

    merged_cues = _merge_brief_cues(
        [
            SubtitleCue(
                start_seconds=group[0].start_seconds,
                end_seconds=group[-1].end_seconds,
                text="\n".join(_wrap_words([item.text for item in group], max_chars_per_line=max_chars_per_line)),
            )
            for group in provisional
        ],
        max_words_per_cue=max_words_per_cue,
        max_chars_per_line=max_chars_per_line,
    )
    return merged_cues


def _align_script_words_to_reference(
    reference_words: list[TimedWord],
    script_words: list[str],
    *,
    min_word_duration_seconds: float,
) -> list[TimedWord]:
    if not reference_words:
        cursor = 0.0
        timed_words: list[TimedWord] = []
        for word in script_words:
            next_cursor = cursor + min_word_duration_seconds
            timed_words.append(TimedWord(text=word, start_seconds=cursor, end_seconds=next_cursor))
            cursor = next_cursor
        return timed_words

    reference_tokens = [_normalize_alignment_token(word.text) for word in reference_words]
    script_tokens = [_normalize_alignment_token(word) for word in script_words]
    mapping = _match_script_indices_to_reference(reference_tokens, script_tokens)
    average_duration = max(
        sum(max(word.end_seconds - word.start_seconds, min_word_duration_seconds) for word in reference_words)
        / max(len(reference_words), 1),
        min_word_duration_seconds,
    )

    aligned: list[TimedWord | None] = [None] * len(script_words)
    for script_index, reference_index in mapping.items():
        reference_word = reference_words[reference_index]
        aligned[script_index] = TimedWord(
            text=script_words[script_index],
            start_seconds=reference_word.start_seconds,
            end_seconds=reference_word.end_seconds,
        )

    index = 0
    while index < len(script_words):
        if aligned[index] is not None:
            index += 1
            continue
        span_start = index
        while index < len(script_words) and aligned[index] is None:
            index += 1
        span_end = index - 1

        previous_index = span_start - 1
        while previous_index >= 0 and aligned[previous_index] is None:
            previous_index -= 1
        next_index = span_end + 1
        while next_index < len(script_words) and aligned[next_index] is None:
            next_index += 1

        if previous_index >= 0 and aligned[previous_index] is not None:
            window_start = aligned[previous_index].end_seconds
        elif next_index < len(script_words) and aligned[next_index] is not None:
            estimated_span = average_duration * (span_end - span_start + 1)
            window_start = max(aligned[next_index].start_seconds - estimated_span, 0.0)
        else:
            window_start = 0.0

        if next_index < len(script_words) and aligned[next_index] is not None:
            window_end = aligned[next_index].start_seconds
        elif previous_index >= 0 and aligned[previous_index] is not None:
            window_end = window_start + (average_duration * (span_end - span_start + 1))
        else:
            window_end = average_duration * (span_end - span_start + 1)

        if window_end <= window_start:
            window_end = window_start + (average_duration * (span_end - span_start + 1))

        span_words = script_words[span_start : span_end + 1]
        span_weights = [max(len(_normalize_alignment_token(word)), 1) for word in span_words]
        total_weight = sum(span_weights)
        cursor = window_start
        total_duration = max(window_end - window_start, min_word_duration_seconds * len(span_words))
        for offset, word in enumerate(span_words):
            if offset == len(span_words) - 1:
                next_cursor = window_end
            else:
                proportional_duration = total_duration * (span_weights[offset] / total_weight)
                next_cursor = min(window_end, cursor + max(proportional_duration, min_word_duration_seconds))
            if next_cursor <= cursor:
                next_cursor = cursor + min_word_duration_seconds
            aligned[span_start + offset] = TimedWord(
                text=word,
                start_seconds=cursor,
                end_seconds=next_cursor,
            )
            cursor = next_cursor

    return [word for word in aligned if word is not None]


def _match_script_indices_to_reference(reference_tokens: list[str], script_tokens: list[str]) -> dict[int, int]:
    reference_length = len(reference_tokens)
    script_length = len(script_tokens)
    if not reference_length or not script_length:
        return {}

    gap_penalty = -1.0
    scores = [[0.0] * (script_length + 1) for _ in range(reference_length + 1)]
    trace = [[""] * (script_length + 1) for _ in range(reference_length + 1)]

    for reference_index in range(1, reference_length + 1):
        scores[reference_index][0] = reference_index * gap_penalty
        trace[reference_index][0] = "up"
    for script_index in range(1, script_length + 1):
        scores[0][script_index] = script_index * gap_penalty
        trace[0][script_index] = "left"

    for reference_index in range(1, reference_length + 1):
        for script_index in range(1, script_length + 1):
            similarity = _alignment_similarity(
                reference_tokens[reference_index - 1],
                script_tokens[script_index - 1],
            )
            diagonal = scores[reference_index - 1][script_index - 1] + similarity
            up = scores[reference_index - 1][script_index] + gap_penalty
            left = scores[reference_index][script_index - 1] + gap_penalty

            best_score = diagonal
            best_trace = "diag"
            if up > best_score:
                best_score = up
                best_trace = "up"
            if left > best_score:
                best_score = left
                best_trace = "left"
            scores[reference_index][script_index] = best_score
            trace[reference_index][script_index] = best_trace

    mapping: dict[int, int] = {}
    reference_index = reference_length
    script_index = script_length
    while reference_index > 0 or script_index > 0:
        direction = trace[reference_index][script_index]
        if direction == "diag":
            similarity = _alignment_similarity(
                reference_tokens[reference_index - 1],
                script_tokens[script_index - 1],
            )
            if similarity > 0:
                mapping[script_index - 1] = reference_index - 1
            reference_index -= 1
            script_index -= 1
        elif direction == "up":
            reference_index -= 1
        else:
            script_index -= 1
    return mapping


def _alignment_similarity(reference_token: str, script_token: str) -> float:
    if not reference_token or not script_token:
        return -3.0
    if reference_token == script_token:
        return 4.0
    ratio = SequenceMatcher(None, reference_token, script_token).ratio()
    if ratio >= 0.9:
        return 3.0
    if ratio >= 0.72:
        return 1.5
    return -3.0


def _normalize_alignment_token(word: str) -> str:
    stripped = _ALIGNMENT_TOKEN_CLEAN_RE.sub("", word.strip().lower())
    if not stripped:
        return ""
    if stripped.isdigit():
        return _NUMBER_WORDS.get(stripped, stripped)
    return _ALIGNMENT_NON_ALNUM_RE.sub("", stripped)


def _should_force_break_after(word: str) -> bool:
    return bool(_SENTENCE_END_RE.search(word.strip()))


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
            if (
                len(combined_words) <= max_words_per_cue + 1
                and not _should_force_break_after(current_words[-1])
                and not _starts_new_sentence(next_words[0])
            ):
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


def _starts_new_sentence(word: str) -> bool:
    cleaned = word.strip()
    if not cleaned:
        return False
    first_character = cleaned[0]
    return first_character.isupper() or first_character.isdigit()


def _build_ass_events_for_cue(
    cue: SubtitleCue,
    *,
    style_name: str,
    primary_color: str,
    highlight_color: str,
    y_position: int,
) -> list[str]:
    line_groups = [line.split() for line in cue.text.splitlines() if line.strip()]
    word_positions = [
        (line_index, word_index)
        for line_index, words in enumerate(line_groups)
        for word_index in range(len(words))
    ]
    if not word_positions:
        return []

    total_duration = max(cue.end_seconds - cue.start_seconds, 0.3)
    weights = [
        max(len(line_groups[line_index][word_index].strip(".,:;!?")), 1)
        for line_index, word_index in word_positions
    ]
    total_weight = sum(weights)
    cursor = cue.start_seconds
    events: list[str] = []

    for index, (line_index, word_index) in enumerate(word_positions):
        if index == len(word_positions) - 1:
            next_cursor = cue.end_seconds
        else:
            proportional = total_duration * (weights[index] / total_weight)
            next_cursor = cursor + max(proportional, 0.06)
        if next_cursor > cue.end_seconds:
            next_cursor = cue.end_seconds
        event_text = _render_ass_highlighted_text(
            line_groups,
            highlight_line=line_index,
            highlight_word=word_index,
            primary_color=primary_color,
            highlight_color=highlight_color,
            y_position=y_position,
        )
        events.append(
            (
                f"Dialogue: 0,{_format_ass_timestamp(cursor)},{_format_ass_timestamp(next_cursor)},"
                f"{style_name},,0,0,0,,{event_text}"
            )
        )
        cursor = next_cursor
    return events


def _render_ass_highlighted_text(
    line_groups: list[list[str]],
    *,
    highlight_line: int,
    highlight_word: int,
    primary_color: str,
    highlight_color: str,
    y_position: int,
) -> str:
    rendered_lines: list[str] = []
    for line_index, words in enumerate(line_groups):
        rendered_words: list[str] = []
        for word_index, word in enumerate(words):
            escaped = _escape_ass_text(word)
            if line_index == highlight_line and word_index == highlight_word:
                rendered_words.append(
                    f"{{\\1c{highlight_color}\\bord4}}{escaped}{{\\1c{primary_color}\\bord3}}"
                )
            else:
                rendered_words.append(escaped)
        rendered_lines.append(" ".join(rendered_words))
    return f"{{\\an5\\pos(540,{y_position})}}" + r"\N".join(rendered_lines)


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", "(").replace("}", ")")


def _format_ass_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    centiseconds = int(round((seconds - int(seconds)) * 100))
    if centiseconds == 100:
        centiseconds = 0
        whole_seconds += 1
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"
