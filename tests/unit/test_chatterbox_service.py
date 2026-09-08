from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.services.chatterbox_service import ChatterboxService


def test_chatterbox_discovers_supported_reference_audio(tmp_path: Path) -> None:
    sample_dir = tmp_path / "voice_samples"
    sample_dir.mkdir()
    first = sample_dir / "voice-01.m4a"
    second = sample_dir / "voice-02.wav"
    ignored = sample_dir / "notes.txt"
    first.write_bytes(b"voice")
    second.write_bytes(b"voice")
    ignored.write_text("not audio", encoding="utf-8")

    service = ChatterboxService(Settings(xtts_speaker_wav_dir=sample_dir))

    assert service.discover_reference_sources() == [first.resolve(), second.resolve()]


def test_chatterbox_mock_synthesis_writes_audio(tmp_path: Path) -> None:
    output_path = tmp_path / "preview.wav"
    service = ChatterboxService(Settings(mock_mode=True, xtts_speaker_wav_dir=tmp_path))

    service.synthesize(text="This is a test.", output_path=output_path)

    assert output_path.exists()
    assert output_path.stat().st_size > 0
