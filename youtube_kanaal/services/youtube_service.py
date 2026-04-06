from __future__ import annotations

import json
import time
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
            token_path = self.settings.youtube_token_path
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(json.dumps({"mock": True}, indent=2), encoding="utf-8")
            return token_path

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

        credentials = None
        token_path = self.settings.youtube_token_path
        if token_path.exists() and not force:
            credentials = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_UPLOAD_SCOPE)

        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        elif not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secret_path),
                YOUTUBE_UPLOAD_SCOPE,
            )
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
        response_path: Path,
    ) -> UploadMetadata:
        if self.settings.mock_mode:
            payload = {
                "id": "mock-video-id",
                "status": {"privacyStatus": privacy_status},
                "snippet": {"title": title},
            }
            write_json(response_path, payload)
            return UploadMetadata(
                youtube_video_id="mock-video-id",
                privacy_status=privacy_status,
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
                    "privacyStatus": privacy_status,
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
            privacy_status=privacy_status,
            response_path=response_path,
            uploaded=True,
        )

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
