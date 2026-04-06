from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from youtube_kanaal.exceptions import ConfigurationError


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _expand_path(value: str | Path | None) -> Path | None:
    if value in (None, ""):
        return None
    return Path(value).expanduser().resolve()


class Settings(BaseSettings):
    """Application settings loaded from .env and environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    app_name: str = Field(default="youtube-kanaal", validation_alias=AliasChoices("YOUTUBE_KANAAL_APP_NAME", "APP_NAME"))
    app_env: str = Field(default="development", validation_alias=AliasChoices("YOUTUBE_KANAAL_ENV", "APP_ENV"))
    app_debug: bool = Field(default=False, validation_alias=AliasChoices("YOUTUBE_KANAAL_DEBUG"))
    mock_mode: bool = Field(default=False, validation_alias=AliasChoices("MOCK_MODE"))

    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        validation_alias=AliasChoices("OLLAMA_BASE_URL"),
    )
    ollama_model: str = Field(
        default="llama3.1:8b-instruct",
        validation_alias=AliasChoices("OLLAMA_MODEL"),
    )
    pexels_api_key: str | None = Field(default=None, validation_alias=AliasChoices("PEXELS_API_KEY"))

    youtube_client_secret_path: Path = Field(
        default_factory=lambda: project_root() / "data" / "credentials" / "client_secret.json",
        validation_alias=AliasChoices("YOUTUBE_CLIENT_SECRET_PATH"),
    )
    youtube_token_path: Path = Field(
        default_factory=lambda: project_root() / "data" / "credentials" / "youtube_token.json",
        validation_alias=AliasChoices("YOUTUBE_TOKEN_PATH"),
    )
    default_privacy_status: str = Field(
        default="private",
        validation_alias=AliasChoices("DEFAULT_PRIVACY_STATUS"),
    )

    ffmpeg_binary: str = Field(default="ffmpeg", validation_alias=AliasChoices("FFMPEG_BINARY"))
    piper_binary: str = Field(default="piper", validation_alias=AliasChoices("PIPER_BINARY"))
    piper_voice_model_path: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("PIPER_VOICE_MODEL_PATH"),
    )
    default_piper_voice: str = Field(
        default="en_US-lessac-medium",
        validation_alias=AliasChoices("DEFAULT_PIPER_VOICE"),
    )
    whisper_cpp_binary: str = Field(
        default="whisper-cli",
        validation_alias=AliasChoices("WHISPER_CPP_BINARY"),
    )
    whisper_model_path: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("WHISPER_MODEL_PATH"),
    )

    output_dir: Path = Field(
        default_factory=lambda: project_root() / "output",
        validation_alias=AliasChoices("OUTPUT_DIR"),
    )
    cache_dir: Path = Field(
        default_factory=lambda: project_root() / "cache",
        validation_alias=AliasChoices("CACHE_DIR"),
    )
    data_dir: Path = Field(
        default_factory=lambda: project_root() / "data",
        validation_alias=AliasChoices("DATA_DIR"),
    )
    logs_dir: Path = Field(
        default_factory=lambda: project_root() / "logs",
        validation_alias=AliasChoices("LOGS_DIR"),
    )
    downloads_dir: Path = Field(
        default_factory=lambda: Path.home() / "Downloads",
        validation_alias=AliasChoices("DOWNLOADS_DIR"),
    )
    database_path: Path = Field(
        default_factory=lambda: project_root() / "data" / "youtube_kanaal.db",
        validation_alias=AliasChoices("DATABASE_PATH"),
    )

    network_timeout_seconds: int = Field(default=30, validation_alias=AliasChoices("NETWORK_TIMEOUT_SECONDS"))
    retry_attempts: int = Field(default=3, validation_alias=AliasChoices("RETRY_ATTEMPTS"))
    pexels_results_per_query: int = Field(default=12, validation_alias=AliasChoices("PEXELS_RESULTS_PER_QUERY"))
    similarity_threshold: float = Field(default=0.86, validation_alias=AliasChoices("SIMILARITY_THRESHOLD"))
    min_short_duration_seconds: int = Field(
        default=20,
        validation_alias=AliasChoices("MIN_SHORT_DURATION_SECONDS"),
    )
    max_short_duration_seconds: int = Field(
        default=35,
        validation_alias=AliasChoices("MAX_SHORT_DURATION_SECONDS"),
    )
    subtitle_font_name: str = Field(default="Arial", validation_alias=AliasChoices("SUBTITLE_FONT_NAME"))
    subtitle_font_size: int = Field(default=18, validation_alias=AliasChoices("SUBTITLE_FONT_SIZE"))
    subtitle_margin_v: int = Field(default=280, validation_alias=AliasChoices("SUBTITLE_MARGIN_V"))
    subtitle_outline: int = Field(default=2, validation_alias=AliasChoices("SUBTITLE_OUTLINE"))

    @field_validator(
        "youtube_client_secret_path",
        "youtube_token_path",
        "piper_voice_model_path",
        "whisper_model_path",
        "output_dir",
        "cache_dir",
        "data_dir",
        "logs_dir",
        "downloads_dir",
        "database_path",
        mode="before",
    )
    @classmethod
    def _normalize_paths(cls, value: str | Path | None) -> Path | None:
        return _expand_path(value)

    @field_validator("default_privacy_status")
    @classmethod
    def _validate_privacy_status(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"private", "unlisted", "public"}:
            raise ValueError("DEFAULT_PRIVACY_STATUS must be private, unlisted, or public.")
        return normalized

    @model_validator(mode="after")
    def _validate_duration_window(self) -> "Settings":
        if self.min_short_duration_seconds >= self.max_short_duration_seconds:
            raise ValueError("MIN_SHORT_DURATION_SECONDS must be lower than MAX_SHORT_DURATION_SECONDS.")
        return self

    def ensure_directories(self) -> None:
        for path in (self.output_dir, self.cache_dir, self.data_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.youtube_client_secret_path.parent.mkdir(parents=True, exist_ok=True)
        self.youtube_token_path.parent.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def require(self, field_name: str, reason: str) -> None:
        value = getattr(self, field_name)
        if value in (None, "", Path()):
            raise ConfigurationError(f"Missing required setting {field_name!r}: {reason}")


def load_settings(**overrides: object) -> Settings:
    settings = Settings(**overrides)
    settings.ensure_directories()
    return settings
