from __future__ import annotations

from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.services.xtts_service import XTTSService


def test_xtts_service_discovers_supported_reference_audio(tmp_path) -> None:
    sample_dir = tmp_path / "voice_samples"
    sample_dir.mkdir()
    (sample_dir / "memo-01.m4a").write_bytes(b"voice")
    (sample_dir / "memo-02.wav").write_bytes(b"voice")
    (sample_dir / "notes.txt").write_text("ignore", encoding="utf-8")

    settings = Settings(
        narration_engine="xtts",
        xtts_speaker_wav_dir=sample_dir,
        xtts_max_reference_clips=5,
    )

    discovered = XTTSService(settings).discover_reference_sources()

    assert [path.name for path in discovered] == ["memo-01.m4a", "memo-02.wav"]


def test_xtts_prepare_reference_audio_trims_long_samples(monkeypatch, tmp_path) -> None:
    sample_dir = tmp_path / "voice_samples"
    sample_dir.mkdir()
    source_path = sample_dir / "memo-01.m4a"
    source_path.write_bytes(b"voice")
    settings = Settings(
        narration_engine="xtts",
        ffmpeg_binary="ffmpeg",
        xtts_speaker_wav_dir=sample_dir,
        xtts_reference_max_seconds=30,
    )
    commands: list[list[str]] = []

    def fake_run_command(command, **kwargs):
        commands.append(list(command))
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"wav")
        return None

    monkeypatch.setattr("youtube_kanaal.services.xtts_service.run_command", fake_run_command)

    prepared = XTTSService(settings).prepare_reference_audio()

    assert len(prepared) == 1
    assert commands
    assert "-t" in commands[0]
    assert "30" in commands[0]
