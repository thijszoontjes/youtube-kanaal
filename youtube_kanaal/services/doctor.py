from __future__ import annotations

import platform
import sys

from youtube_kanaal.config import Settings
from youtube_kanaal.models.run import DoctorCheck, DoctorReport
from youtube_kanaal.services.ollama_service import OllamaService
from youtube_kanaal.services.pexels_service import PexelsService
from youtube_kanaal.utils.files import is_writable_directory
from youtube_kanaal.utils.process import command_exists


class DoctorService:
    """Environment diagnostics for local setup."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ollama = OllamaService(settings)
        self.pexels = PexelsService(settings)

    def run(self) -> DoctorReport:
        checks = [
            self._python_check(),
            self._binary_check("FFmpeg", self.settings.ffmpeg_binary),
            self._ollama_check(),
            self._ollama_model_check(),
            self._binary_check("Piper", self.settings.piper_binary),
            self._piper_voice_check(),
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

    def _binary_check(self, label: str, command: str) -> DoctorCheck:
        ok = command_exists(command)
        return DoctorCheck(
            name=label,
            status="ok" if ok else "fail",
            details=command,
            action=None if ok else f"Install or configure {label}.",
        )

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

    def _piper_voice_check(self) -> DoctorCheck:
        inferred = self.settings.piper_voice_model_path or (
            self.settings.cache_dir / "piper" / f"{self.settings.default_piper_voice}.onnx"
        )
        ok = inferred.exists()
        return DoctorCheck(
            name="Piper voice model",
            status="ok" if ok else "warn",
            details=str(inferred),
            action=None if ok else "Download a Piper voice model and point PIPER_VOICE_MODEL_PATH at it.",
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
