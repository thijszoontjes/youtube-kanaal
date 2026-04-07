from __future__ import annotations

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
