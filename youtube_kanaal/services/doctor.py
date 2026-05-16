from __future__ import annotations

import platform
import sys

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.models.run import DoctorCheck, DoctorReport
from youtube_kanaal.services.kokoro_service import KokoroService
from youtube_kanaal.services.ollama_service import OllamaService
from youtube_kanaal.services.pexels_service import PexelsService
from youtube_kanaal.services.piper_service import PiperService
from youtube_kanaal.services.xtts_service import XTTSService
from youtube_kanaal.utils.files import is_writable_directory
from youtube_kanaal.utils.process import command_exists, run_command


class DoctorService:
    """Environment diagnostics for local setup."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ollama = OllamaService(settings)
        self.pexels = PexelsService(settings)

    def run(self) -> DoctorReport:
        narration_details = self.settings.narration_engine
        if self.settings.narration_engine == "kokoro" and self.settings.kokoro_fallback_to_piper:
            narration_details = "kokoro with Piper fallback"
        if self.settings.narration_engine == "xtts" and self.settings.xtts_fallback_to_piper:
            narration_details = "xtts with Piper fallback"
        checks = [
            self._python_check(),
            self._binary_check("FFmpeg", self.settings.ffmpeg_binary),
            self._ollama_check(),
            self._ollama_model_check(),
            DoctorCheck(
                name="Narration engine",
                status="ok",
                details=narration_details,
                action=None,
            ),
            *self._narration_checks(),
            self._binary_check("whisper.cpp", self.settings.whisper_cpp_binary),
            self._whisper_model_check(),
            self._pexels_key_check(),
            self._youtube_oauth_check(),
            self._downloads_check(),
        ]
        return DoctorReport(checks=checks)

    def _python_check(self) -> DoctorCheck:
        ok = sys.version_info >= (3, 11)
        return DoctorCheck(
            name="Python version",
            status="ok" if ok else "fail",
            details=f"{platform.python_version()}",
            action=None if ok else "Install Python 3.11 or newer.",
        )

    def _narration_checks(self) -> list[DoctorCheck]:
        if self.settings.narration_engine == "kokoro":
            return self._kokoro_checks()
        if self.settings.narration_engine == "xtts":
            return self._xtts_checks()
        return [
            self._binary_check("Piper", self.settings.piper_binary),
            self._piper_voice_check(),
        ]

    def _binary_check(self, label: str, command: str) -> DoctorCheck:
        ok = command_exists(command)
        return DoctorCheck(
            name=label,
            status="ok" if ok else "fail",
            details=command,
            action=None if ok else f"Install or configure {label}.",
        )

    def _kokoro_checks(self) -> list[DoctorCheck]:
        kokoro = KokoroService(self.settings)
        runtime_ok, runtime_reason = kokoro.runtime_ready()
        piper_ready, _ = PiperService(self.settings).runtime_ready()
        fallback_available = self.settings.kokoro_fallback_to_piper and piper_ready
        checks = [
            DoctorCheck(
                name="Kokoro",
                status="ok" if runtime_ok else ("warn" if fallback_available else "fail"),
                details=(
                    f"voice={self.settings.kokoro_voice}, "
                    f"lang={self.settings.kokoro_lang_code}, "
                    f"speed={self.settings.kokoro_speed}"
                    + (f" | {runtime_reason}" if runtime_reason else "")
                ),
                action=None
                if runtime_ok
                else (
                    "Install Kokoro with `pip install 'kokoro>=0.9.4'` and install espeak-ng. "
                    "Until then the pipeline falls back to Piper."
                    if fallback_available
                    else "Install Kokoro with `pip install 'kokoro>=0.9.4'` and install espeak-ng."
                ),
            )
        ]
        if self.settings.kokoro_fallback_to_piper and not runtime_ok:
            checks.extend(
                [
                    self._binary_check("Piper", self.settings.piper_binary),
                    self._piper_voice_check(),
                ]
            )
        return checks

    def _ollama_check(self) -> DoctorCheck:
        ok = self.ollama.is_available()
        return DoctorCheck(
            name="Ollama reachable",
            status="ok" if ok else "fail",
            details=self.settings.ollama_base_url,
            action=None if ok else "Start Ollama and make sure the local API is reachable.",
        )

    def _ollama_model_check(self) -> DoctorCheck:
        models = self.ollama.list_models()
        ok = self.settings.ollama_model in models
        return DoctorCheck(
            name="Ollama model",
            status="ok" if ok else "fail",
            details=self.settings.ollama_model,
            action=None if ok else f"Run: ollama pull {self.settings.ollama_model}",
        )

    def _xtts_checks(self) -> list[DoctorCheck]:
        xtts = XTTSService(self.settings)
        samples = xtts.discover_reference_sources()
        samples_ready = bool(samples)
        fallback_enabled = self.settings.xtts_fallback_to_piper
        runtime_ok, runtime_reason = xtts.runtime_ready()
        piper_ready, _ = PiperService(self.settings).runtime_ready()
        runtime_details = (
            f"docker -> {self.settings.xtts_docker_image}"
            if self.settings.xtts_runtime == "docker"
            else f"binary -> {self.settings.xtts_binary}"
        )
        if runtime_reason:
            runtime_details = f"{runtime_details} | {runtime_reason}"
        sample_dir = self.settings.xtts_speaker_wav_dir
        sample_details = (
            f"{len(samples)} sample(s) ready"
            if samples
            else str(sample_dir) if sample_dir else "not configured"
        )
        fallback_runtime_available = fallback_enabled and piper_ready
        return [
            DoctorCheck(
                name="XTTS runtime",
                status="ok" if runtime_ok else ("warn" if fallback_runtime_available else "fail"),
                details=runtime_details,
                action=None
                if runtime_ok
                else self._xtts_runtime_action(
                    samples_ready=samples_ready,
                    fallback_enabled=fallback_enabled,
                    fallback_runtime_available=fallback_runtime_available,
                ),
            ),
            DoctorCheck(
                name="XTTS speaker samples",
                status="ok" if samples else ("warn" if fallback_enabled else "fail"),
                details=sample_details,
                action=None
                if samples
                else (
                    "Add 1-5 English voice memos to XTTS_SPEAKER_WAV_DIR or set XTTS_SPEAKER_WAV_PATH."
                    if not fallback_enabled
                    else "Add 1-5 English voice memos to use your own voice. Until then the pipeline falls back to Piper."
                ),
            ),
            *(
                [
                    self._binary_check("Piper", self.settings.piper_binary),
                    self._piper_voice_check(),
                ]
                if fallback_enabled and (not samples_ready or not runtime_ok)
                else []
            ),
        ]

    def _xtts_runtime_ok(self) -> bool:
        if self.settings.xtts_runtime == "docker":
            return command_exists("docker") and self._xtts_docker_image_ready()
        return command_exists(self.settings.xtts_binary)

    def _xtts_runtime_action(
        self,
        *,
        samples_ready: bool,
        fallback_enabled: bool,
        fallback_runtime_available: bool,
    ) -> str:
        if self.settings.xtts_runtime == "docker":
            if not command_exists("docker"):
                return "Install Docker Desktop or switch to XTTS_RUNTIME=binary with a working tts command."
            if fallback_runtime_available:
                return (
                    "XTTS Docker image is not ready yet. The pipeline can still fall back to Piper, "
                    "but your personal cloned voice will not be used until the image is installed."
                )
            if fallback_enabled and not samples_ready:
                return "XTTS Docker image is not ready yet, but the pipeline can still fall back to Piper until you add voice memos."
            return f"Run: docker pull {self.settings.xtts_docker_image}"
        if fallback_runtime_available:
            return "XTTS is not ready yet. The pipeline can still fall back to Piper, but your personal cloned voice will not be used until XTTS is installed."
        if fallback_enabled and not samples_ready:
            return "XTTS is not ready yet, but the pipeline can still fall back to Piper until you add voice memos."
        return "Install Coqui TTS or set XTTS_RUNTIME=docker."

    def _xtts_docker_image_ready(self) -> bool:
        try:
            run_command(
                [
                    "docker",
                    "image",
                    "inspect",
                    self.settings.xtts_docker_image,
                ],
                timeout_seconds=20,
                stage="doctor",
            )
        except PipelineStageError:
            return False
        return True

    def _piper_voice_check(self) -> DoctorCheck:
        piper = PiperService(self.settings)
        ok, resolved_path, details_reason = piper.describe_voice_model()
        inferred = resolved_path or self.settings.piper_voice_model_path or (
            self.settings.cache_dir / "piper" / f"{self.settings.default_piper_voice}.onnx"
        )
        status = "ok" if ok and details_reason is None else ("warn" if ok else "fail")
        action = None
        if not ok:
            action = "Download a Piper voice model and point PIPER_VOICE_MODEL_PATH at it."
        elif details_reason:
            action = "Re-download or replace the configured Piper voice model to stop using the fallback voice."
        return DoctorCheck(
            name="Piper voice model",
            status=status,
            details=f"{inferred}" if details_reason is None else f"{inferred} | {details_reason}",
            action=action,
        )

    def _whisper_model_check(self) -> DoctorCheck:
        path = self.settings.whisper_model_path
        ok = bool(path and path.exists())
        return DoctorCheck(
            name="whisper model path",
            status="ok" if ok else "warn",
            details=str(path) if path else "not configured",
            action=None if ok else "Set WHISPER_MODEL_PATH to a local ggml whisper model.",
        )

    def _pexels_key_check(self) -> DoctorCheck:
        if not self.settings.pexels_api_key:
            return DoctorCheck(
                name="Pexels API key",
                status="fail",
                details="missing",
                action="Set PEXELS_API_KEY in .env.",
            )
        valid = self.pexels.validate_credentials()
        return DoctorCheck(
            name="Pexels API key",
            status="ok" if valid else "warn",
            details="present",
            action=None if valid else "Run auth-pexels or verify the key value in .env.",
        )

    def _youtube_oauth_check(self) -> DoctorCheck:
        ok = self.settings.youtube_client_secret_path.exists()
        return DoctorCheck(
            name="YouTube OAuth client JSON",
            status="ok" if ok else "fail",
            details=str(self.settings.youtube_client_secret_path),
            action=None if ok else "Place the Google Cloud desktop client JSON at the configured path.",
        )

    def _downloads_check(self) -> DoctorCheck:
        ok = is_writable_directory(self.settings.downloads_dir)
        return DoctorCheck(
            name="Downloads folder",
            status="ok" if ok else "fail",
            details=str(self.settings.downloads_dir),
            action=None if ok else "Create the directory or update DOWNLOADS_DIR to a writable location.",
        )
