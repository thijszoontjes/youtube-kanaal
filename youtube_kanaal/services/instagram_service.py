from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.models import InstagramUploadMetadata
from youtube_kanaal.utils.files import write_json


class InstagramService:
    """Instagram Graph API adapter for publishing Reels."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def ensure_configured(self) -> None:
        if self.settings.mock_mode:
            return
        missing = []
        if not self.settings.instagram_user_id:
            missing.append("INSTAGRAM_USER_ID")
        if not self.settings.instagram_access_token:
            missing.append("INSTAGRAM_ACCESS_TOKEN")
        if missing:
            raise PipelineStageError(
                stage="instagram_upload",
                message="Instagram upload is not configured.",
                probable_cause=(
                    f"Set {', '.join(missing)} in .env. The account must be an Instagram Professional "
                    "account with Content Publishing access."
                ),
            )

    def upload_reel(
        self,
        *,
        video_path: Path,
        caption: str,
        response_path: Path,
    ) -> InstagramUploadMetadata:
        if self.settings.mock_mode:
            payload = {
                "mock": True,
                "container": {"id": "mock-instagram-container-id"},
                "publish": {"id": "mock-instagram-media-id"},
            }
            write_json(response_path, payload)
            return InstagramUploadMetadata(
                instagram_media_id="mock-instagram-media-id",
                container_id="mock-instagram-container-id",
                response_path=response_path,
                uploaded=True,
            )

        self.ensure_configured()
        if not video_path.exists() or video_path.stat().st_size == 0:
            raise PipelineStageError(
                stage="instagram_upload",
                message="Instagram Reel video file was not found or is empty.",
                probable_cause=f"Expected a rendered MP4 at: {video_path}",
            )

        upload_log: dict[str, Any] = {
            "video_path": str(video_path),
            "api_version": self.settings.instagram_api_version,
        }
        with httpx.Client(timeout=60.0) as client:
            container = self._create_resumable_container(client=client, caption=caption)
            upload_log["container"] = container
            container_id = str(container["id"])
            upload_uri = str(container["uri"])

            upload_payload = self._upload_video_binary(
                client=client,
                upload_uri=upload_uri,
                video_path=video_path,
            )
            upload_log["upload"] = upload_payload

            status_payload = self._wait_until_container_finished(client=client, container_id=container_id)
            upload_log["status"] = status_payload

            publish_payload = self._publish_container(client=client, container_id=container_id)
            upload_log["publish"] = publish_payload

            media_id = str(publish_payload["id"])
            permalink = self._fetch_permalink(client=client, media_id=media_id)
            upload_log["permalink"] = permalink

        write_json(response_path, upload_log)
        return InstagramUploadMetadata(
            instagram_media_id=media_id,
            container_id=container_id,
            response_path=response_path,
            uploaded=True,
            permalink=permalink,
        )

    def _create_resumable_container(self, *, client: httpx.Client, caption: str) -> dict[str, Any]:
        payload = self._post_graph(
            client=client,
            path=f"/{self.settings.instagram_user_id}/media",
            data={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": caption[:2200],
                "share_to_feed": str(self.settings.instagram_share_to_feed).lower(),
                "access_token": self.settings.instagram_access_token,
            },
        )
        if not payload.get("id") or not payload.get("uri"):
            raise PipelineStageError(
                stage="instagram_upload",
                message="Instagram did not return a resumable upload session.",
                probable_cause="The API response did not include both 'id' and 'uri'.",
            )
        return payload

    def _upload_video_binary(
        self,
        *,
        client: httpx.Client,
        upload_uri: str,
        video_path: Path,
    ) -> dict[str, Any]:
        file_size = video_path.stat().st_size
        headers = {
            "Authorization": f"OAuth {self.settings.instagram_access_token}",
            "offset": "0",
            "file_size": str(file_size),
            "Content-Type": "application/octet-stream",
        }
        try:
            with video_path.open("rb") as file_handle:
                response = client.post(upload_uri, headers=headers, content=file_handle, timeout=300.0)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._raise_api_error(exc.response, default_message="Instagram video binary upload failed.")
        except httpx.HTTPError as exc:
            raise PipelineStageError(
                stage="instagram_upload",
                message="Instagram video binary upload failed.",
                probable_cause=str(exc),
            ) from exc
        payload = self._json_response(response)
        if payload.get("success") is not True:
            raise PipelineStageError(
                stage="instagram_upload",
                message="Instagram did not accept the uploaded video file.",
                probable_cause=str(payload.get("message") or payload),
            )
        return payload

    def _wait_until_container_finished(self, *, client: httpx.Client, container_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.settings.instagram_processing_timeout_seconds
        last_payload: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last_payload = self._get_graph(
                client=client,
                path=f"/{container_id}",
                params={
                    "fields": "status_code,status",
                    "access_token": self.settings.instagram_access_token,
                },
            )
            status_code = str(last_payload.get("status_code") or "").upper()
            if status_code == "FINISHED":
                return last_payload
            if status_code in {"ERROR", "EXPIRED"}:
                raise PipelineStageError(
                    stage="instagram_upload",
                    message=f"Instagram Reel processing failed with status {status_code}.",
                    probable_cause=str(last_payload.get("status") or last_payload),
                )
            time.sleep(self.settings.instagram_poll_interval_seconds)
        raise PipelineStageError(
            stage="instagram_upload",
            message="Instagram Reel processing timed out.",
            probable_cause=f"Last container status: {last_payload or 'no status returned'}",
        )

    def _publish_container(self, *, client: httpx.Client, container_id: str) -> dict[str, Any]:
        payload = self._post_graph(
            client=client,
            path=f"/{self.settings.instagram_user_id}/media_publish",
            data={
                "creation_id": container_id,
                "access_token": self.settings.instagram_access_token,
            },
        )
        if not payload.get("id"):
            raise PipelineStageError(
                stage="instagram_upload",
                message="Instagram did not return a media ID after publishing.",
                probable_cause=str(payload),
            )
        return payload

    def _fetch_permalink(self, *, client: httpx.Client, media_id: str) -> str | None:
        try:
            payload = self._get_graph(
                client=client,
                path=f"/{media_id}",
                params={
                    "fields": "permalink",
                    "access_token": self.settings.instagram_access_token,
                },
            )
        except PipelineStageError:
            return None
        permalink = payload.get("permalink")
        return str(permalink) if permalink else None

    def _post_graph(self, *, client: httpx.Client, path: str, data: dict[str, object]) -> dict[str, Any]:
        try:
            response = client.post(self._graph_url(path), data=data)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._raise_api_error(exc.response, default_message="Instagram Graph API request failed.")
        except httpx.HTTPError as exc:
            raise PipelineStageError(
                stage="instagram_upload",
                message="Instagram Graph API request failed.",
                probable_cause=str(exc),
            ) from exc
        return self._json_response(response)

    def _get_graph(self, *, client: httpx.Client, path: str, params: dict[str, object]) -> dict[str, Any]:
        try:
            response = client.get(self._graph_url(path), params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._raise_api_error(exc.response, default_message="Instagram Graph API request failed.")
        except httpx.HTTPError as exc:
            raise PipelineStageError(
                stage="instagram_upload",
                message="Instagram Graph API request failed.",
                probable_cause=str(exc),
            ) from exc
        return self._json_response(response)

    def _graph_url(self, path: str) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"https://graph.facebook.com/{self.settings.instagram_api_version}{normalized_path}"

    def _json_response(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise PipelineStageError(
                stage="instagram_upload",
                message="Instagram returned a non-JSON response.",
                probable_cause=response.text[:500],
            ) from exc
        if not isinstance(payload, dict):
            raise PipelineStageError(
                stage="instagram_upload",
                message="Instagram returned an unexpected response shape.",
                probable_cause=str(payload),
            )
        return payload

    def _raise_api_error(self, response: httpx.Response, *, default_message: str) -> None:
        payload: dict[str, Any] = {}
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                payload = parsed
        except ValueError:
            pass
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        message = str(error.get("message") or response.text or default_message)
        code = error.get("code")
        subcode = error.get("error_subcode")
        trace = error.get("fbtrace_id")
        probable_cause = self._probable_cause_from_error(message=message, code=code)
        details = f"{message}"
        if code is not None:
            details += f" (code={code}"
            if subcode is not None:
                details += f", subcode={subcode}"
            details += ")"
        if trace:
            details += f" fbtrace_id={trace}"
        raise PipelineStageError(
            stage="instagram_upload",
            message=default_message,
            probable_cause=f"{probable_cause} Meta response: {details}",
        )

    def _probable_cause_from_error(self, *, message: str, code: object) -> str:
        lowered = message.lower()
        if code == 190 or "access token" in lowered or "token" in lowered:
            return "The Instagram access token is invalid, expired, or missing required scopes."
        if code in {10, 200} or "permission" in lowered or "not authorized" in lowered:
            return (
                "The token/account does not have Instagram Content Publishing permission. "
                "Check instagram_basic, instagram_content_publish, Page access, and App Review/live mode."
            )
        if "unsupported post request" in lowered or "object does not exist" in lowered:
            return "The Instagram user ID is wrong, not connected to the token, or unavailable to this app."
        if "professional" in lowered or "business" in lowered or "creator" in lowered:
            return "The Instagram account must be a Professional account connected to the app."
        if "format" in lowered or "video" in lowered:
            return "Instagram rejected the video file; check MP4/H.264/AAC, duration, aspect ratio, and file size."
        return "Check the Instagram account, token scopes, app mode/review status, and media requirements."
