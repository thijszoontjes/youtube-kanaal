from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from youtube_kanaal.config import Settings
from youtube_kanaal.services.macos_say_service import MacOSSayService


def test_runtime_ready_checks_platform_binary_and_selected_voice(monkeypatch) -> None:
    service = MacOSSayService(Settings(_env_file=None, macos_say_voice="Alex"))
    monkeypatch.setattr("youtube_kanaal.services.macos_say_service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("youtube_kanaal.services.macos_say_service.command_exists", lambda _command: True)
    monkeypatch.setattr(
        "youtube_kanaal.services.macos_say_service.run_command",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="Alex en_US # Hello\nSamantha en_US # Hello", stderr=""),
    )

    assert service.runtime_ready() == (True, None)


def test_runtime_ready_rejects_missing_voice(monkeypatch) -> None:
    service = MacOSSayService(Settings(_env_file=None, macos_say_voice="Missing Voice"))
    monkeypatch.setattr("youtube_kanaal.services.macos_say_service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("youtube_kanaal.services.macos_say_service.command_exists", lambda _command: True)
    monkeypatch.setattr(
        "youtube_kanaal.services.macos_say_service.run_command",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="Alex en_US # Hello", stderr=""),
    )

    ready, reason = service.runtime_ready()

    assert ready is False
    assert "Missing Voice" in (reason or "")


def test_synthesize_uses_voice_rate_and_temporary_text_file(monkeypatch, tmp_path: Path) -> None:
    service = MacOSSayService(Settings(_env_file=None, macos_say_voice="Alex", macos_say_rate=145))
    commands: list[list[str]] = []

    def fake_run_command(command, **_kwargs):
        commands.append(list(command))
        Path(command[6]).write_bytes(b"AIFF audio")
        assert Path(command[-1]).read_text(encoding="utf-8") == "A narrated sentence."

    monkeypatch.setattr("youtube_kanaal.services.macos_say_service.run_command", fake_run_command)
    output = tmp_path / "voice.aiff"

    service.synthesize(text="A narrated sentence.", output_path=output)

    assert commands[0][1:6] == ["-v", "Alex", "-r", "145", "-o"]
    assert output.read_bytes() == b"AIFF audio"
    assert not (tmp_path / "voice_say_input.txt").exists()


def test_runtime_ready_rejects_non_macos_hosts(monkeypatch) -> None:
    service = MacOSSayService(Settings(_env_file=None))
    monkeypatch.setattr("youtube_kanaal.services.macos_say_service.platform.system", lambda: "Linux")

    ready, reason = service.runtime_ready()

    assert ready is False
    assert "only on macOS" in (reason or "")
