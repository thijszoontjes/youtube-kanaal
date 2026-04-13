from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.models.assets import UploadMetadata
from youtube_kanaal.utils.files import write_json

YOUTUBE_UPLOAD_SCOPE = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubeService:
    """YouTube OAuth and upload adapter."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def authenticate(self, *, force: bool = False) -> Path:
        if self.settings.mock_mode:
            return self.settings.youtube_token_path

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:
            raise PipelineStageError(
                stage="youtube_auth",
                message="Google API libraries are not installed.",
                probable_cause="Install the project dependencies first.",
            ) from exc

        client_secret_path = self.settings.youtube_client_secret_path
        if not client_secret_path.exists():
            raise PipelineStageError(
                stage="youtube_auth",
                message="YouTube OAuth client JSON was not found.",
                probable_cause="Place your Google Cloud desktop OAuth client JSON at the configured path.",
                details_path=client_secret_path,
            )
        self._validate_client_secret_file(client_secret_path)

        credentials = None
        token_path = self.settings.youtube_token_path
        if token_path.exists() and not force:
            try:
                credentials = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_UPLOAD_SCOPE)
            except (ValueError, json.JSONDecodeError, OSError):
                self._backup_invalid_token(token_path)
                credentials = None

        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        elif not credentials or not credentials.valid:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(client_secret_path),
                    YOUTUBE_UPLOAD_SCOPE,
                )
            except ValueError as exc:
                raise PipelineStageError(
                    stage="youtube_auth",
                    message="YouTube OAuth client JSON is not in the correct Google format.",
                    probable_cause=(
                        "Download the Desktop app OAuth client JSON from Google Cloud and replace "
                        f"{client_secret_path.name}. The file must include installed/client_id, "
                        "client_secret, auth_uri, token_uri, and redirect_uris."
                    ),
                    details_path=client_secret_path,
                ) from exc
            credentials = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
        return token_path

    def upload_video(
        self,
        *,
        video_path: Path,
        title: str,
        description: str,
        hashtags: list[str],
        privacy_status: str,
        scheduled_publish_at: datetime | None,
        response_path: Path,
    ) -> UploadMetadata:
        resolved_privacy_status, publish_at_payload, normalized_publish_at = self._resolve_upload_schedule(
            privacy_status=privacy_status,
            scheduled_publish_at=scheduled_publish_at,
        )
        if self.settings.mock_mode:
            payload = {
                "id": "mock-video-id",
                "status": {
                    "privacyStatus": resolved_privacy_status,
                    **({"publishAt": publish_at_payload} if publish_at_payload else {}),
                },
                "snippet": {"title": title},
            }
            write_json(response_path, payload)
            return UploadMetadata(
                youtube_video_id="mock-video-id",
                privacy_status=resolved_privacy_status,
                scheduled_publish_at=normalized_publish_at,
                response_path=response_path,
                uploaded=True,
            )

        credentials = self._load_credentials()
        try:
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            raise PipelineStageError(
                stage="youtube_upload",
                message="Google API client libraries are not installed.",
                probable_cause="Install the project dependencies first.",
            ) from exc

        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": hashtags,
                    "categoryId": "27",
                },
                "status": {
                    "privacyStatus": resolved_privacy_status,
                    **({"publishAt": publish_at_payload} if publish_at_payload else {}),
                    "selfDeclaredMadeForKids": False,
                },
            },
            media_body=MediaFileUpload(str(video_path), chunksize=-1, resumable=True),
        )

        try:
            payload = self._perform_resumable_upload(request, HttpError)
        except HttpError as exc:
            raise PipelineStageError(
                stage="youtube_upload",
                message="YouTube upload failed.",
                probable_cause=str(exc),
            ) from exc

        write_json(response_path, payload)
        return UploadMetadata(
            youtube_video_id=payload.get("id"),
            privacy_status=resolved_privacy_status,
            scheduled_publish_at=normalized_publish_at,
            response_path=response_path,
            uploaded=True,
        )

    def _resolve_upload_schedule(
        self,
        *,
        privacy_status: str,
        scheduled_publish_at: datetime | None,
    ) -> tuple[str, str | None, datetime | None]:
        normalized_privacy = privacy_status.strip().lower()
        if scheduled_publish_at is None:
            return normalized_privacy, None, None
        if scheduled_publish_at.tzinfo is None or scheduled_publish_at.utcoffset() is None:
            raise PipelineStageError(
                stage="youtube_upload",
                message="Scheduled publish time must be timezone-aware.",
                probable_cause="Pass an ISO datetime with timezone or let the CLI build the schedule.",
            )
        now_utc = datetime.now(timezone.utc)
        publish_at_utc = scheduled_publish_at.astimezone(timezone.utc)
        if publish_at_utc <= now_utc:
            raise PipelineStageError(
                stage="youtube_upload",
                message="Scheduled publish time must be in the future.",
                probable_cause="Choose a later date or time for the planned upload.",
            )
        if normalized_privacy != "private":
            raise PipelineStageError(
                stage="youtube_upload",
                message="Scheduled YouTube uploads must use privacy status 'private'.",
                probable_cause="YouTube only accepts publishAt on private videos.",
            )
        publish_at_payload = publish_at_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return normalized_privacy, publish_at_payload, scheduled_publish_at

    def _load_credentials(self) -> Any:
        self.authenticate(force=False)
        try:
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise PipelineStageError(
                stage="youtube_upload",
                message="Google OAuth credentials library is unavailable.",
                probable_cause="Install the project dependencies first.",
            ) from exc
        return Credentials.from_authorized_user_file(
            str(self.settings.youtube_token_path),
            YOUTUBE_UPLOAD_SCOPE,
        )

    def _perform_resumable_upload(self, request: Any, http_error_cls: type[Exception]) -> dict[str, Any]:
        response = None
        attempts = 0
        while response is None:
            try:
                _, response = request.next_chunk()
            except http_error_cls as exc:  # type: ignore[misc]
                attempts += 1
                if attempts >= self.settings.retry_attempts:
                    raise
                time.sleep(min(2**attempts, 8))
                continue
        return dict(response)

    def _backup_invalid_token(self, token_path: Path) -> None:
        if not token_path.exists():
            return
        backup_path = token_path.with_name(
            f"{token_path.stem}.invalid-{time.strftime('%Y%m%d-%H%M%S')}{token_path.suffix}"
        )
        token_path.replace(backup_path)

    def _validate_client_secret_file(self, client_secret_path: Path) -> None:
        try:
            payload = json.loads(client_secret_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PipelineStageError(
                stage="youtube_auth",
                message="YouTube OAuth client JSON could not be parsed.",
                probable_cause="Replace the file with the original JSON downloaded from Google Cloud.",
                details_path=client_secret_path,
            ) from exc

        if not isinstance(payload, dict):
            raise PipelineStageError(
                stage="youtube_auth",
                message="YouTube OAuth client JSON has an invalid top-level structure.",
                probable_cause="Replace the file with the original Google OAuth client JSON.",
                details_path=client_secret_path,
            )

        client_config = payload.get("installed") or payload.get("web")
        required_fields = {"client_id", "client_secret", "auth_uri", "token_uri", "redirect_uris"}
        if not isinstance(client_config, dict) or not required_fields.issubset(client_config):
            raise PipelineStageError(
                stage="youtube_auth",
                message="YouTube OAuth client JSON is incomplete.",
                probable_cause=(
                    "The file must contain an installed or web client with client_id, client_secret, "
                    "auth_uri, token_uri, and redirect_uris. Download a fresh OAuth Desktop app JSON "
                    "from Google Cloud."
                ),
                details_path=client_secret_path,
            )
