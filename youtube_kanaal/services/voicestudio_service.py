from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError


class VoiceStudioService:
    """Generate narration with a saved voice in the local VoiceStudio app."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.voicestudio_api_url.rstrip("/")

    def runtime_ready(self) -> tuple[bool, str | None]:
        if not self.settings.voicestudio_voice_id.strip():
            return False, "Set VOICESTUDIO_VOICE_ID to the saved clone's profile ID."
        try:
            health = self._get_json("/health")
            if health.get("status") != "ok":
                return False, "VoiceStudio is reachable but its backend is not ready."
            catalog = self._get_json("/v1/audio/voices")
            voices = catalog.get("voices", [])
            if not any(
                voice.get("voice_id") == self.settings.voicestudio_voice_id
                and voice.get("type") == "profile"
                for voice in voices
                if isinstance(voice, dict)
            ):
                return False, "The configured VoiceStudio profile is not available in the local app."
            engine = next(
                (item for item in catalog.get("engines", []) if item.get("id") == "omnivoice"),
                None,
            )
            if not engine or not engine.get("available"):
                return False, (engine or {}).get("reason") or "The OmniVoice engine is not ready in VoiceStudio."
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", None) or str(exc)
            return False, f"Cannot reach the local VoiceStudio API at {self.base_url}: {reason}"
        return True, None

    def synthesize(self, *, text: str, output_path: Path) -> None:
        payload = {
            "model": "omnivoice",
            "voice": self.settings.voicestudio_voice_id,
            "input": text,
            "language": "en",
            "response_format": "wav",
            "speed": self.settings.voicestudio_speed,
        }
        if self.settings.voicestudio_instruct.strip():
            payload["instruct"] = self.settings.voicestudio_instruct.strip()
        request = Request(
            f"{self.base_url}/v1/audio/speech",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "audio/wav"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.settings.voicestudio_timeout_seconds) as response:
                audio = response.read()
        except HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", errors="replace").strip()
            raise PipelineStageError(
                stage="narration_generation",
                message="VoiceStudio could not synthesize the narration.",
                probable_cause=detail or f"VoiceStudio returned HTTP {exc.code}.",
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise PipelineStageError(
                stage="narration_generation",
                message="Could not reach VoiceStudio while synthesizing narration.",
                probable_cause=str(getattr(exc, "reason", None) or exc),
            ) from exc

        if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise PipelineStageError(
                stage="narration_generation",
                message="VoiceStudio returned a response that was not a WAV audio file.",
                probable_cause=audio[:300].decode("utf-8", errors="replace"),
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)

    def _get_json(self, path: str) -> dict[str, object]:
        request = Request(f"{self.base_url}{path}", headers={"Accept": "application/json"})
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read())
        if not isinstance(result, dict):
            raise ValueError(f"Unexpected response from {path}.")
        return result
