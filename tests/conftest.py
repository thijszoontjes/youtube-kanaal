from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def configured_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    output_dir = tmp_path / "output"
    cache_dir = tmp_path / "cache"
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    downloads_dir = tmp_path / "downloads"
    credentials_dir = data_dir / "credentials"
    credentials_dir.mkdir(parents=True, exist_ok=True)
    client_secret_path = credentials_dir / "client_secret.json"
    client_secret_path.write_text('{"installed": {"client_id": "mock"}}', encoding="utf-8")

    env = {
        "YOUTUBE_KANAAL_ENV": "test",
        "YOUTUBE_KANAAL_DEBUG": "false",
        "MOCK_MODE": "true",
        "OUTPUT_DIR": str(output_dir),
        "CACHE_DIR": str(cache_dir),
        "DATA_DIR": str(data_dir),
        "LOGS_DIR": str(logs_dir),
        "DOWNLOADS_DIR": str(downloads_dir),
        "DATABASE_PATH": str(data_dir / "youtube_kanaal.db"),
        "YOUTUBE_CLIENT_SECRET_PATH": str(client_secret_path),
        "YOUTUBE_TOKEN_PATH": str(credentials_dir / "youtube_token.json"),
        "PEXELS_API_KEY": "mock-key",
        "ALLOW_PLACEHOLDER_VIDEO": "true",
        "PIPER_VOICE_MODEL_PATH": str(cache_dir / "piper" / "mock.onnx"),
        "WHISPER_MODEL_PATH": str(cache_dir / "whisper" / "ggml-base.en.bin"),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return {
        "output_dir": output_dir,
        "cache_dir": cache_dir,
        "data_dir": data_dir,
        "logs_dir": logs_dir,
        "downloads_dir": downloads_dir,
        "client_secret_path": client_secret_path,
    }
