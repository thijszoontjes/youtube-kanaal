from __future__ import annotations

from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.services.narration_service import NarrationService


class StubPiperService:
    def __init__(self, *, ready: bool = True, reason: str | None = None) -> None:
        self.ready = ready
        self.reason = reason
        self.calls = 0

    def runtime_ready(self) -> tuple[bool, str | None]:
        return self.ready, self.reason

    def synthesize(self, *, text: str, output_path: Path, logger=None) -> Path:
        self.calls += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"piper")
        return output_path


class StubKokoroService:
    def __init__(
        self,
        *,
        ready: bool = True,
        reason: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.ready = ready
        self.reason = reason
        self.error = error
        self.calls = 0

    def runtime_ready(self) -> tuple[bool, str | None]:
        return self.ready, self.reason

    def synthesize(self, *, text: str, output_path: Path, logger=None) -> Path:
        self.calls += 1
        if self.error:
            raise self.error
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"kokoro")
        return output_path


class StubXTTSService:
    def __init__(
        self,
        *,
        sources: list[Path] | None = None,
        ready: bool = True,
        reason: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.sources = sources or []
        self.ready = ready
        self.reason = reason
        self.error = error
        self.calls = 0

    def discover_reference_sources(self, *, logger=None) -> list[Path]:
        return self.sources

    def runtime_ready(self) -> tuple[bool, str | None]:
        return self.ready, self.reason

    def synthesize(self, *, text: str, output_path: Path, logger=None) -> Path:
        self.calls += 1
        if self.error:
            raise self.error
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"xtts")
        return output_path


def test_narration_service_uses_kokoro_by_default_when_ready() -> None:
    settings = Settings(narration_engine="kokoro")
    kokoro = StubKokoroService(ready=True)
    piper = StubPiperService(ready=True)

    service = NarrationService(
        settings,
        kokoro_service=kokoro,
        piper_service=piper,
        xtts_service=StubXTTSService(),
    )

    inspection = service.inspect()

    assert inspection.resolved_engine == "kokoro"
    assert inspection.kokoro_ready is True


def test_narration_service_falls_back_to_piper_when_kokoro_missing(tmp_path) -> None:
    settings = Settings(narration_engine="kokoro", kokoro_fallback_to_piper=True)
    kokoro = StubKokoroService(ready=False, reason="Kokoro package not installed")
    piper = StubPiperService(ready=True)
    service = NarrationService(
        settings,
        kokoro_service=kokoro,
        piper_service=piper,
        xtts_service=StubXTTSService(),
    )

    result = service.synthesize(text="hello", output_path=tmp_path / "out.wav")

    assert result.engine_used == "piper"
    assert "not installed" in (result.fallback_reason or "")
    assert piper.calls == 1
    assert kokoro.calls == 0


def test_narration_service_falls_back_to_piper_when_kokoro_synthesis_fails(tmp_path) -> None:
    settings = Settings(narration_engine="kokoro", kokoro_fallback_to_piper=True)
    kokoro = StubKokoroService(
        ready=True,
        error=PipelineStageError(
            stage="narration_generation",
            message="Kokoro synthesis failed.",
            probable_cause="espeak-ng crashed",
        ),
    )
    piper = StubPiperService(ready=True)
    service = NarrationService(
        settings,
        kokoro_service=kokoro,
        piper_service=piper,
        xtts_service=StubXTTSService(),
    )

    result = service.synthesize(text="hello", output_path=tmp_path / "out.wav")

    assert result.engine_used == "piper"
    assert "espeak-ng crashed" in (result.fallback_reason or "")
    assert piper.calls == 1
    assert kokoro.calls == 1


def test_narration_service_falls_back_to_piper_when_xtts_samples_missing_and_piper_ready(tmp_path) -> None:
    settings = Settings(
        narration_engine="xtts",
        xtts_speaker_wav_dir=tmp_path / "missing-samples",
        xtts_fallback_to_piper=True,
    )

    service = NarrationService(
        settings,
        piper_service=StubPiperService(ready=True),
        xtts_service=StubXTTSService(sources=[]),
    )

    inspection = service.inspect()

    assert inspection.resolved_engine == "piper"
    assert "no valid reference audio files" in (inspection.fallback_reason or "").lower()


def test_narration_service_uses_xtts_when_reference_audio_exists_and_runtime_is_ready(tmp_path) -> None:
    sample_dir = tmp_path / "voice_samples"
    sample_dir.mkdir()
    sample_path = sample_dir / "memo.wav"
    sample_path.write_bytes(b"voice")
    settings = Settings(
        narration_engine="xtts",
        xtts_speaker_wav_dir=sample_dir,
        xtts_fallback_to_piper=True,
    )

    service = NarrationService(
        settings,
        piper_service=StubPiperService(ready=True),
        xtts_service=StubXTTSService(sources=[sample_path], ready=True),
    )

    inspection = service.inspect()

    assert inspection.resolved_engine == "xtts"
    assert inspection.fallback_reason is None


def test_narration_service_falls_back_to_piper_when_xtts_synthesis_fails(tmp_path) -> None:
    sample_path = tmp_path / "memo.m4a"
    sample_path.write_bytes(b"voice")
    settings = Settings(
        narration_engine="xtts",
        xtts_speaker_wav_dir=tmp_path,
        xtts_fallback_to_piper=True,
    )
    piper = StubPiperService(ready=True)
    xtts = StubXTTSService(
        sources=[sample_path],
        ready=True,
        error=PipelineStageError(
            stage="narration_generation",
            message="XTTS synthesis failed.",
            probable_cause="docker run failed because the container exited early",
        ),
    )
    service = NarrationService(settings, piper_service=piper, xtts_service=xtts)

    result = service.synthesize(text="hello", output_path=tmp_path / "out.wav")

    assert result.engine_used == "piper"
    assert "container exited early" in (result.fallback_reason or "")
    assert piper.calls == 1
    assert xtts.calls == 1
