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


def test_align_script_to_reference_srt_keeps_sentence_starts_near_reference_timing() -> None:
    reference_srt = """1
00:00:00,000 --> 00:00:01,280
Here are three facts

2
00:00:01,280 --> 00:00:02,300
about jalfish.

3
00:00:02,300 --> 00:00:03,680
First, jalfish have

4
00:00:03,680 --> 00:00:05,390
been on the planet for

5
00:00:05,390 --> 00:00:07,280
over 60 to 50 million

6
00:00:07,280 --> 00:00:07,760
years.

7
00:00:07,760 --> 00:00:09,580
Second, jalfish are not

8
00:00:09,580 --> 00:00:11,250
actually fish despite

9
00:00:11,250 --> 00:00:12,270
their aquatic

10
00:00:12,270 --> 00:00:13,320
environment.

11
00:00:13,320 --> 00:00:15,680
Third, many jalfish

12
00:00:15,680 --> 00:00:17,390
species are bioluminescent,

13
00:00:17,390 --> 00:00:18,300
meaning they can

14
00:00:18,300 --> 00:00:19,160
produce light.

15
00:00:19,160 --> 00:00:20,670
That is why jalfish
"""
    script_text = (
        "Here are 3 facts about jellyfish. First, Jellyfish have been on the planet for over 650 "
        "million years. Second, Jellyfish are not actually fish, despite their aquatic environment. "
        "Third, Many jellyfish species are bioluminescent, meaning they can produce light. "
        "That is why jellyfish stands out."
    )

    aligned = align_script_to_reference_srt(reference_srt, script_text)
    cues = parse_srt_text(aligned)
    cue_texts = [cue.text.replace("\n", " ") for cue in cues]

    assert all("years. Second," not in text for text in cue_texts)
    assert all("light. That" not in text for text in cue_texts)

    second_cue = next(cue for cue in cues if "Second," in cue.text)
    that_cue = next(cue for cue in cues if cue.text.replace("\n", " ").startswith("That"))

    assert second_cue.start_seconds >= 7.76
    assert that_cue.start_seconds >= 19.16


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
