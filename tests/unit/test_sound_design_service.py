from __future__ import annotations

import subprocess
import wave
from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.services.sound_design_service import SoundDesignService


def _write_wav(path: Path, duration_seconds: float = 0.25) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(48000 * duration_seconds)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(48000)
        wav_file.writeframes(b"\x00\x00" * frames)


def test_sound_design_service_returns_dry_audio_when_disabled(tmp_path: Path) -> None:
    narration_path = tmp_path / "narration.wav"
    _write_wav(narration_path, 1.0)
    settings = Settings(sound_design_enabled=False)

    asset = SoundDesignService(settings).build_mix(
        narration_path=narration_path,
        duration_seconds=12.0,
        working_dir=tmp_path / "audio",
    )

    assert asset.mixed_path == narration_path
    assert asset.applied is False
    assert asset.effect_count == 0
    assert "disabled" in (asset.fallback_reason or "").lower()


def test_sound_design_service_builds_procedural_mix(monkeypatch, tmp_path: Path) -> None:
    narration_path = tmp_path / "narration.wav"
    _write_wav(narration_path, 2.0)
    settings = Settings(
        sound_design_enabled=True,
        mock_mode=False,
        ffmpeg_binary="ffmpeg",
        sound_design_custom_audio_filename=None,
    )
    calls: list[list[str]] = []

    def fake_run_command(command, **kwargs):
        calls.append(list(command))
        _write_wav(Path(command[-1]), 0.2 if "narration_soundscape.wav" not in command[-1] else 2.0)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("youtube_kanaal.services.sound_design_service.run_command", fake_run_command)

    asset = SoundDesignService(settings).build_mix(
        narration_path=narration_path,
        duration_seconds=24.0,
        working_dir=tmp_path / "audio",
    )

    assert asset.applied is True
    assert asset.effect_count == 9
    assert asset.mixed_path.exists()
    assert len(asset.stem_paths) == 9
    assert asset.music_path is not None
    assert asset.music_path.name == "background_music.wav"
    assert asset.music_profile is not None
    assert any(path.name == "background_music.wav" for path in asset.stem_paths)
    assert any(path.name == "room_tone.wav" for path in asset.stem_paths)
    assert any(path.name == "riser.wav" for path in asset.stem_paths)
    assert calls[-1][-1].endswith("narration_soundscape.wav")


def test_sound_design_service_uses_single_custom_audio_file_when_configured(monkeypatch, tmp_path: Path) -> None:
    narration_path = tmp_path / "narration.wav"
    _write_wav(narration_path, 2.0)
    custom_dir = tmp_path / "custom"
    custom_audio_path = custom_dir / "sting.mp3"
    custom_dir.mkdir(parents=True, exist_ok=True)
    custom_audio_path.write_bytes(b"custom-audio")
    settings = Settings(
        sound_design_enabled=True,
        mock_mode=False,
        ffmpeg_binary="ffmpeg",
        sound_design_custom_audio_dir=custom_dir,
        sound_design_custom_audio_filename="sting.mp3",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr("youtube_kanaal.services.sound_design_service.random.uniform", lambda start, end: 7.25)

    def fake_run_command(command, **kwargs):
        calls.append(list(command))
        _write_wav(Path(command[-1]), 2.0)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("youtube_kanaal.services.sound_design_service.run_command", fake_run_command)

    asset = SoundDesignService(settings).build_mix(
        narration_path=narration_path,
        duration_seconds=24.0,
        working_dir=tmp_path / "audio",
    )

    assert asset.applied is True
    assert asset.effect_count == 1
    assert asset.mixed_path.exists()
    assert asset.stem_paths == [custom_audio_path.resolve()]
    assert str(custom_audio_path.resolve()) in calls[-1]
    filter_complex = calls[-1][calls[-1].index("-filter_complex") + 1]
    assert "atrim=0:16.75,volume=-7dB,adelay=7250:all=1[fx1]" in filter_complex


def test_sound_design_service_uses_procedural_music_when_custom_audio_file_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    narration_path = tmp_path / "narration.wav"
    _write_wav(narration_path, 1.0)
    settings = Settings(
        sound_design_enabled=True,
        mock_mode=False,
        ffmpeg_binary="ffmpeg",
        sound_design_custom_audio_dir=tmp_path / "custom",
        sound_design_custom_audio_filename="missing.mp3",
    )
    calls: list[list[str]] = []

    def fake_run_command(command, **kwargs):
        calls.append(list(command))
        _write_wav(Path(command[-1]), 1.0)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("youtube_kanaal.services.sound_design_service.run_command", fake_run_command)

    asset = SoundDesignService(settings).build_mix(
        narration_path=narration_path,
        duration_seconds=12.0,
        working_dir=tmp_path / "audio",
    )

    assert asset.mixed_path != narration_path
    assert asset.applied is True
    assert asset.effect_count > 0
    assert asset.music_path is not None
    assert asset.mixed_path.exists()
    assert calls[-1][-1].endswith("narration_soundscape.wav")
    assert "not found" in (asset.fallback_reason or "").lower()
