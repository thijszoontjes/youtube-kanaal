from __future__ import annotations

import base64
from pathlib import Path

from youtube_kanaal.config import Settings


class OnlineRuntimeService:
    """Materialize secrets and voice samples from environment variables for remote runners."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def materialize_from_environment(self) -> list[dict[str, str]]:
        written: list[dict[str, str]] = []
        text_specs = [
            (
                "youtube-client-secret",
                self.settings.youtube_client_secret_path,
                "ONLINE_YOUTUBE_CLIENT_SECRET_JSON",
                "ONLINE_YOUTUBE_CLIENT_SECRET_JSON_B64",
            ),
            (
                "youtube-token",
                self.settings.youtube_token_path,
                "ONLINE_YOUTUBE_TOKEN_JSON",
                "ONLINE_YOUTUBE_TOKEN_JSON_B64",
            ),
        ]
        for kind, target_path, raw_env, b64_env in text_specs:
            payload, source_env = self._read_text_payload(raw_env=raw_env, b64_env=b64_env)
            if payload is None:
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(payload, encoding="utf-8")
            written.append({"kind": kind, "path": str(target_path), "source_env": source_env})

        voice_payload, voice_source = self._read_binary_payload(
            raw_env="ONLINE_XTTS_REFERENCE_AUDIO_RAW_B64",
            b64_env="ONLINE_XTTS_REFERENCE_AUDIO_B64",
        )
        if voice_payload is not None:
            file_name = Path(
                self._get_env("ONLINE_XTTS_REFERENCE_AUDIO_FILENAME") or "reference-online.m4a"
            ).name
            target_dir = self.settings.xtts_speaker_wav_dir or (self.settings.data_dir / "voice_samples" / "en")
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / file_name
            target_path.write_bytes(voice_payload)
            written.append({"kind": "xtts-reference-audio", "path": str(target_path), "source_env": voice_source})
        return written

    def _read_text_payload(self, *, raw_env: str, b64_env: str) -> tuple[str | None, str]:
        raw_value = self._get_env(raw_env)
        if raw_value:
            return raw_value, raw_env
        b64_value = self._get_env(b64_env)
        if not b64_value:
            return None, ""
        return base64.b64decode(b64_value).decode("utf-8"), b64_env

    def _read_binary_payload(self, *, raw_env: str, b64_env: str) -> tuple[bytes | None, str]:
        raw_value = self._get_env(raw_env)
        if raw_value:
            return base64.b64decode(raw_value), raw_env
        b64_value = self._get_env(b64_env)
        if not b64_value:
            return None, ""
        return base64.b64decode(b64_value), b64_env

    def _get_env(self, name: str) -> str | None:
        import os

        value = os.getenv(name)
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None
