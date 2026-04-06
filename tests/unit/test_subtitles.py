from __future__ import annotations

from youtube_kanaal.utils.subtitles import (
    align_script_to_reference_srt,
    build_ass_from_srt_text,
    normalize_whisper_srt,
    parse_srt_text,
)


def test_normalize_whisper_srt_splits_long_cues_into_shorter_chunks() -> None:
    source_srt = """1
00:00:00,000 --> 00:00:04,000
The rings are made up of ice and rock particles that range in size from tiny dust grains to boulders.
"""

    normalized = normalize_whisper_srt(source_srt, max_words_per_cue=4, max_chars_per_line=18)
    cues = parse_srt_text(normalized)

    assert len(cues) >= 3
    assert all(cue.text.strip() for cue in cues)
    assert all(len(cue.text.replace("\n", " ").split()) <= 4 for cue in cues)
    assert "dust grains" in normalized


def test_align_script_to_reference_srt_uses_known_script_text() -> None:
    reference_srt = """1
00:00:00,000 --> 00:00:03,000
1. Iceland is located

2
00:00:03,000 --> 00:00:06,000
on the Mid-Atlantic Ridge.
"""
    script_text = "Here are 3 facts about Iceland. Fact 1: Iceland is located on the Mid-Atlantic Ridge."

    aligned = align_script_to_reference_srt(reference_srt, script_text)
    cues = parse_srt_text(aligned)

    assert cues[0].text.startswith("Here")
    assert "Fact 1:" in aligned


def test_build_ass_from_srt_text_creates_word_highlight_events() -> None:
    srt_text = """1
00:00:00,000 --> 00:00:02,000
Here are 3 facts
"""

    ass_text = build_ass_from_srt_text(
        srt_text,
        font_name="Arial",
        font_size=20,
        margin_v=640,
        outline=3,
        primary_color="&H00FFFFFF",
        highlight_color="&H006BFF7C",
        outline_color="&H00000000",
        back_color="&H64000000",
    )

    assert "[V4+ Styles]" in ass_text
    assert "Dialogue:" in ass_text
    assert "\\1c&H006BFF7C" in ass_text
    assert "\\pos(540,1280)" in ass_text
