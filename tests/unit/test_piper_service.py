from __future__ import annotations

from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.services.piper_service import PiperService


def _settings_with_cache(tmp_path: Path) -> Settings:
    return Settings(
        piper_binary="piper",
        piper_voice_model_path=tmp_path / "cache" / "piper" / "en_US-john-medium.onnx",
        cache_dir=tmp_path / "cache",
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "output",
        downloads_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "youtube_kanaal.db",
    )


def test_piper_service_uses_valid_cached_fallback_when_configured_model_is_corrupt(tmp_path, monkeypatch) -> None:
    settings = _settings_with_cache(tmp_path)
    configured_model_path = settings.piper_voice_model_path
    assert configured_model_path is not None
    configured_model_path.parent.mkdir(parents=True, exist_ok=True)
    configured_model_path.write_bytes(b"bad!")
    fallback_model_path = configured_model_path.parent / "en_US-lessac-medium.onnx"
    fallback_model_path.write_bytes(b"0" * 1_200_000)

    monkeypatch.setattr("youtube_kanaal.services.piper_service.command_exists", lambda command: True)
    monkeypatch.setattr(
        "youtube_kanaal.services.piper_service.PiperService._probe_onnx_model",
        lambda self, path: None,
    )

    service = PiperService(settings)
    ready, reason = service.runtime_ready()
    ok, resolved_path, description = service.describe_voice_model()

    assert ready is True
    assert reason is None
    assert ok is True
    assert resolved_path == fallback_model_path
    assert description is not None
    assert "Falling back" in description


def test_piper_service_passes_fallback_model_to_piper_binary(tmp_path, monkeypatch) -> None:
    settings = _settings_with_cache(tmp_path)
    configured_model_path = settings.piper_voice_model_path
    assert configured_model_path is not None
    configured_model_path.parent.mkdir(parents=True, exist_ok=True)
    configured_model_path.write_bytes(b"bad!")
    fallback_model_path = configured_model_path.parent / "en_US-lessac-medium.onnx"
    fallback_model_path.write_bytes(b"0" * 1_200_000)

    commands: list[list[str]] = []

    def fake_run_command(command, **kwargs):
        commands.append(list(command))
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"wave")
        return None

    monkeypatch.setattr("youtube_kanaal.services.piper_service.command_exists", lambda command: True)
    monkeypatch.setattr(
        "youtube_kanaal.services.piper_service.PiperService._probe_onnx_model",
        lambda self, path: None,
    )
    monkeypatch.setattr("youtube_kanaal.services.piper_service.run_command", fake_run_command)

    service = PiperService(settings)
    output_path = tmp_path / "output" / "narration.wav"
    service.synthesize(text="hello", output_path=output_path)

    assert output_path.exists()
    assert commands
    assert str(fallback_model_path) in commands[0]
