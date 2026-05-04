from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.services.kokoro_service import KokoroService
from youtube_kanaal.services.piper_service import PiperService
from youtube_kanaal.services.xtts_service import XTTSService


@dataclass(frozen=True)
class NarrationInspection:
    requested_engine: str
    resolved_engine: str
    reference_sources: list[Path] = field(default_factory=list)
    fallback_reason: str | None = None
    kokoro_ready: bool = False
    kokoro_reason: str | None = None
    xtts_runtime_ready: bool = False
    xtts_runtime_reason: str | None = None
    piper_ready: bool = False
    piper_reason: str | None = None


@dataclass(frozen=True)
class NarrationSynthesisResult:
    output_path: Path
    inspection: NarrationInspection

    @property
    def engine_used(self) -> str:
        return self.inspection.resolved_engine

    @property
    def requested_engine(self) -> str:
        return self.inspection.requested_engine

    @property
    def fallback_reason(self) -> str | None:
        return self.inspection.fallback_reason

    @property
    def reference_sources(self) -> list[Path]:
        return self.inspection.reference_sources


class NarrationService:
    """Dispatch narration synthesis to the configured engine."""

    def __init__(
        self,
        settings: Settings,
        *,
        kokoro_service: KokoroService | None = None,
        piper_service: PiperService | None = None,
        xtts_service: XTTSService | None = None,
    ) -> None:
        self.settings = settings
        self.kokoro = kokoro_service or KokoroService(settings)
        self.piper = piper_service or PiperService(settings)
        self.xtts = xtts_service or XTTSService(settings)

    def inspect(self, *, logger: logging.Logger | None = None) -> NarrationInspection:
        requested_engine = self.settings.narration_engine
        kokoro_ready, kokoro_reason = self.kokoro.runtime_ready()
        piper_ready, piper_reason = self.piper.runtime_ready()
        if requested_engine == "piper":
            return NarrationInspection(
                requested_engine=requested_engine,
                resolved_engine="piper",
                kokoro_ready=kokoro_ready,
                kokoro_reason=kokoro_reason,
                piper_ready=piper_ready,
                piper_reason=piper_reason,
            )
        if requested_engine == "kokoro":
            fallback_reason = None if kokoro_ready else kokoro_reason or "Kokoro runtime is not ready."
            resolved_engine = "kokoro"
            if fallback_reason and self.settings.kokoro_fallback_to_piper and piper_ready:
                resolved_engine = "piper"
            return NarrationInspection(
                requested_engine=requested_engine,
                resolved_engine=resolved_engine,
                fallback_reason=fallback_reason if resolved_engine == "piper" else None,
                kokoro_ready=kokoro_ready,
                kokoro_reason=kokoro_reason,
                piper_ready=piper_ready,
                piper_reason=piper_reason,
            )

        reference_sources = self.xtts.discover_reference_sources(logger=logger)
        xtts_runtime_ready, xtts_runtime_reason = self.xtts.runtime_ready()
        fallback_reason: str | None = None
        resolved_engine = "xtts"

        if not reference_sources:
            fallback_reason = (
                "XTTS custom voice skipped because no valid reference audio files were found in "
                f"{self.settings.xtts_speaker_wav_dir}."
            )
        elif not xtts_runtime_ready:
            fallback_reason = xtts_runtime_reason or "XTTS runtime is not ready."

        if fallback_reason and self.settings.xtts_fallback_to_piper and piper_ready:
            resolved_engine = "piper"

        inspection = NarrationInspection(
            requested_engine=requested_engine,
            resolved_engine=resolved_engine,
            reference_sources=reference_sources,
            fallback_reason=fallback_reason if resolved_engine == "piper" else None,
            kokoro_ready=kokoro_ready,
            kokoro_reason=kokoro_reason,
            xtts_runtime_ready=xtts_runtime_ready,
            xtts_runtime_reason=xtts_runtime_reason,
            piper_ready=piper_ready,
            piper_reason=piper_reason,
        )
        if logger:
            logger.info(
                "narration_resolution: finish",
                extra={
                    "stage": "narration_generation",
                    "requested_engine": inspection.requested_engine,
                    "resolved_engine": inspection.resolved_engine,
                    "fallback_reason": inspection.fallback_reason,
                    "kokoro_ready": inspection.kokoro_ready,
                    "kokoro_reason": inspection.kokoro_reason,
                    "reference_sources": [str(path) for path in inspection.reference_sources],
                    "xtts_runtime_ready": inspection.xtts_runtime_ready,
                    "xtts_runtime_reason": inspection.xtts_runtime_reason,
                    "piper_ready": inspection.piper_ready,
                    "piper_reason": inspection.piper_reason,
                },
            )
        return inspection

    def resolve_engine(self) -> str:
        return self.inspect().resolved_engine

    def synthesize(
        self,
        *,
        text: str,
        output_path: Path,
        logger: logging.Logger | None = None,
    ) -> NarrationSynthesisResult:
        inspection = self.inspect(logger=logger)
        if inspection.resolved_engine == "kokoro":
            try:
                self.kokoro.synthesize(text=text, output_path=output_path, logger=logger)
                return NarrationSynthesisResult(output_path=output_path, inspection=inspection)
            except Exception as exc:
                if self.settings.kokoro_fallback_to_piper and inspection.piper_ready:
                    fallback_reason = self._fallback_reason_from_exception(exc)
                    if logger:
                        logger.warning(
                            "narration_fallback: kokoro_to_piper",
                            extra={
                                "stage": "narration_generation",
                                "requested_engine": "kokoro",
                                "resolved_engine": "piper",
                                "fallback_reason": fallback_reason,
                            },
                        )
                    self.piper.synthesize(text=text, output_path=output_path, logger=logger)
                    return NarrationSynthesisResult(
                        output_path=output_path,
                        inspection=replace(
                            inspection,
                            resolved_engine="piper",
                            fallback_reason=fallback_reason,
                        ),
                    )
                raise

        if inspection.resolved_engine == "xtts":
            try:
                self.xtts.synthesize(text=text, output_path=output_path, logger=logger)
                return NarrationSynthesisResult(output_path=output_path, inspection=inspection)
            except Exception as exc:
                if self.settings.xtts_fallback_to_piper and inspection.piper_ready:
                    fallback_reason = self._fallback_reason_from_exception(exc)
                    if logger:
                        logger.warning(
                            "narration_fallback: xtts_to_piper",
                            extra={
                                "stage": "narration_generation",
                                "requested_engine": "xtts",
                                "resolved_engine": "piper",
                                "fallback_reason": fallback_reason,
                            },
                        )
                    self.piper.synthesize(text=text, output_path=output_path, logger=logger)
                    return NarrationSynthesisResult(
                        output_path=output_path,
                        inspection=replace(
                            inspection,
                            resolved_engine="piper",
                            fallback_reason=fallback_reason,
                        ),
                    )
                raise

        if inspection.fallback_reason and logger:
            logger.warning(
                "narration_fallback: preflight_selection",
                extra={
                    "stage": "narration_generation",
                    "requested_engine": inspection.requested_engine,
                    "resolved_engine": inspection.resolved_engine,
                    "fallback_reason": inspection.fallback_reason,
                },
            )
        self.piper.synthesize(text=text, output_path=output_path, logger=logger)
        return NarrationSynthesisResult(output_path=output_path, inspection=inspection)

    def _fallback_reason_from_exception(self, exc: Exception) -> str:
        if isinstance(exc, PipelineStageError):
            return exc.probable_cause or exc.message
        return str(exc)
