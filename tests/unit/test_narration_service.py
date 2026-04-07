from __future__ import annotations

from youtube_kanaal.config import Settings
from youtube_kanaal.services.narration_service import NarrationService


def test_narration_service_falls_back_to_piper_when_xtts_samples_missing(tmp_path) -> None:
    settings = Settings(
        narration_engine="xtts",
        xtts_speaker_wav_dir=tmp_path / "missing-samples",
        xtts_fallback_to_piper=True,
    )

    assert NarrationService(settings).resolve_engine() == "piper"


def test_narration_service_uses_xtts_when_reference_audio_exists(tmp_path) -> None:
    sample_dir = tmp_path / "voice_samples"
    sample_dir.mkdir()
    (sample_dir / "memo.wav").write_bytes(b"voice")
    settings = Settings(
        narration_engine="xtts",
        xtts_speaker_wav_dir=sample_dir,
        xtts_fallback_to_piper=True,
    )

    assert NarrationService(settings).resolve_engine() == "xtts"
