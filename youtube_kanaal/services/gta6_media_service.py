from __future__ import annotations

import random
import shutil
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.models.assets import VideoClipAsset
from youtube_kanaal.utils.files import write_json
from youtube_kanaal.utils.process import run_command
from youtube_kanaal.utils.subtitles import ideal_clip_count


ROCKSTAR_GTA6_MEDIA_PAGE = "https://www.rockstargames.com/VI/media/screenshots"
ROCKSTAR_GTA6_SCREENSHOTS_ZIP = (
    "https://media-rockstargames-com.akamaized.net/VI/downloads/screenshots/GTAVI_Screenshots.zip"
)


class GTA6MediaService:
    """Build vertical scene clips from Rockstar's official GTA VI screenshots."""

    _MAX_ARCHIVE_BYTES = 120_000_000
    _MAX_IMAGE_BYTES = 20_000_000
    _CLIP_DURATION_SECONDS = 8.0
    _IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fetch_clips(
        self,
        *,
        topic: str,
        target_duration_seconds: float,
        output_dir: Path,
        response_path: Path,
    ) -> list[VideoClipAsset]:
        screenshots_dir = self._ensure_screenshots()
        screenshots = self._screenshots_for_topic(screenshots_dir, topic)
        clip_count = min(ideal_clip_count(target_duration_seconds), len(screenshots))
        if clip_count < 1:
            raise PipelineStageError(
                stage="stock_video_download",
                message="The official Rockstar GTA VI screenshot archive contained no usable images.",
                probable_cause="Inspect the Rockstar media download or clear the cached archive and retry.",
                details_path=response_path,
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        chosen = random.sample(screenshots, clip_count)
        clips: list[VideoClipAsset] = []
        for index, screenshot in enumerate(chosen, start=1):
            relative_name = screenshot.relative_to(screenshots_dir).with_suffix("").as_posix()
            clip_path = output_dir / f"gta6-{index:02d}.mp4"
            self._render_screenshot(screenshot, clip_path)
            clips.append(
                VideoClipAsset(
                    source_id=relative_name,
                    query=topic,
                    source_url=ROCKSTAR_GTA6_MEDIA_PAGE,
                    download_url=ROCKSTAR_GTA6_SCREENSHOTS_ZIP,
                    local_path=clip_path,
                    duration_seconds=self._CLIP_DURATION_SECONDS,
                    width=1080,
                    height=1920,
                    score=10.0,
                    attribution="Official GTA VI screenshot, Rockstar Games",
                )
            )

        write_json(
            response_path,
            {
                "source": "Rockstar Games official GTA VI screenshots",
                "source_page": ROCKSTAR_GTA6_MEDIA_PAGE,
                "archive_url": ROCKSTAR_GTA6_SCREENSHOTS_ZIP,
                "topic": topic,
                "clips": [clip.model_dump(mode="json") for clip in clips],
            },
        )
        return clips

    def _ensure_screenshots(self) -> Path:
        cache_dir = self.settings.cache_dir / "rockstar-gta6"
        screenshots_dir = cache_dir / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        screenshots = self._image_paths(screenshots_dir)
        if screenshots:
            return screenshots_dir

        archive_path = cache_dir / "GTAVI_Screenshots.zip"
        if not archive_path.exists():
            self._download_archive(archive_path)
        try:
            self._extract_screenshots(archive_path, screenshots_dir)
        except (OSError, zipfile.BadZipFile) as exc:
            archive_path.unlink(missing_ok=True)
            raise PipelineStageError(
                stage="stock_video_download",
                message="Could not extract the official Rockstar GTA VI screenshot archive.",
                probable_cause=str(exc),
            ) from exc
        if not self._image_paths(screenshots_dir):
            raise PipelineStageError(
                stage="stock_video_download",
                message="The Rockstar GTA VI screenshot archive contained no supported image files.",
                probable_cause="Check the official archive format or clear the cache and retry.",
            )
        return screenshots_dir

    def _download_archive(self, archive_path: Path) -> None:
        temp_path = archive_path.with_suffix(".zip.part")
        try:
            with httpx.stream("GET", ROCKSTAR_GTA6_SCREENSHOTS_ZIP, follow_redirects=True, timeout=120) as response:
                response.raise_for_status()
                content_length = int(response.headers.get("content-length", "0"))
                if content_length > self._MAX_ARCHIVE_BYTES:
                    raise PipelineStageError(
                        stage="stock_video_download",
                        message="The Rockstar screenshot archive exceeded the allowed download size.",
                        probable_cause=f"Archive size is {content_length} bytes.",
                    )
                total_bytes = 0
                with temp_path.open("wb") as target:
                    for chunk in response.iter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > self._MAX_ARCHIVE_BYTES:
                            raise PipelineStageError(
                                stage="stock_video_download",
                                message="The Rockstar screenshot archive exceeded the allowed download size.",
                                probable_cause=f"Download exceeded {self._MAX_ARCHIVE_BYTES} bytes.",
                            )
                        target.write(chunk)
            temp_path.replace(archive_path)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            if isinstance(exc, PipelineStageError):
                raise
            raise PipelineStageError(
                stage="stock_video_download",
                message="Could not download the official Rockstar GTA VI screenshot archive.",
                probable_cause=str(exc),
            ) from exc

    def _extract_screenshots(self, archive_path: Path, screenshots_dir: Path) -> None:
        with zipfile.ZipFile(archive_path) as archive:
            for entry in archive.infolist():
                relative_path = PurePosixPath(entry.filename)
                if entry.is_dir() or relative_path.is_absolute() or ".." in relative_path.parts:
                    continue
                if Path(relative_path.name).suffix.lower() not in self._IMAGE_SUFFIXES:
                    continue
                if entry.file_size <= 0 or entry.file_size > self._MAX_IMAGE_BYTES:
                    continue
                destination = screenshots_dir.joinpath(*relative_path.parts)
                if not destination.resolve().is_relative_to(screenshots_dir.resolve()):
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)

    def _screenshots_for_topic(self, screenshots_dir: Path, topic: str) -> list[Path]:
        images = self._image_paths(screenshots_dir)
        normalized_topic = topic.casefold()
        if "lucia" in normalized_topic and "jason" in normalized_topic:
            matched = [path for path in images if "people/jason and lucia/" in path.as_posix().casefold()]
        elif "lucia" in normalized_topic:
            matched = [path for path in images if "people/lucia caminos/" in path.as_posix().casefold()]
        elif "jason" in normalized_topic:
            matched = [path for path in images if "people/jason duval/" in path.as_posix().casefold()]
        elif "vice city" in normalized_topic:
            matched = [path for path in images if "places/vice city/" in path.as_posix().casefold()]
        else:
            matched = []
        return matched or images

    def _render_screenshot(self, screenshot_path: Path, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        filter_graph = (
            "[0:v]split[background][foreground];"
            "[background]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=32:16[bg];"
            "[foreground]scale=1170:658:force_original_aspect_ratio=increase,"
            "crop=1080:607:x='(in_w-out_w)/2+18*sin(t*0.22)':"
            "y='(in_h-out_h)/2+10*cos(t*0.18)',setsar=1[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[out]"
        )
        run_command(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-loop",
                "1",
                "-framerate",
                "30",
                "-i",
                str(screenshot_path),
                "-t",
                str(self._CLIP_DURATION_SECONDS),
                "-filter_complex",
                filter_graph,
                "-map",
                "[out]",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "24",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            timeout_seconds=180,
            stage="stock_video_download",
        )

    @staticmethod
    def _image_paths(directory: Path) -> list[Path]:
        return sorted(path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in GTA6MediaService._IMAGE_SUFFIXES)
