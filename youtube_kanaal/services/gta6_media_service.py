from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.models.assets import VideoClipAsset
from youtube_kanaal.utils.files import write_json
from youtube_kanaal.utils.process import run_command
from youtube_kanaal.utils.subtitles import ideal_clip_count


ROCKSTAR_GTA6_MEDIA_PAGE = "https://www.rockstargames.com/VI/media/videos"


@dataclass(frozen=True)
class TrailerSource:
    name: str
    url: str
    duration_seconds: float


_TRAILERS = (
    TrailerSource(
        "trailer-1",
        "https://media-rockstargames-com.akamaized.net/VI/downloads/videos/GTAVI_Trailer_1/GTAVI_Trailer_1.mp4",
        90.03,
    ),
    TrailerSource(
        "trailer-2",
        "https://media-rockstargames-com.akamaized.net/VI/downloads/videos/GTAVI_Trailer_2/GTAVI_Trailer_2.mp4",
        166.73,
    ),
    TrailerSource(
        "launch-trailer",
        "https://media-rockstargames-com.akamaized.net/VI/downloads/videos/GTAVI_Launch_Trailer/GTAVI_Launch_Trailer.mp4",
        89.17,
    ),
)


class GTA6MediaService:
    """Extract real moving scenes from Rockstar's official GTA VI trailers."""

    _CLIP_DURATION_SECONDS = 4.0
    _MAX_CLIPS_PER_SHORT = 7

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
        clip_count = min(ideal_clip_count(target_duration_seconds), self._MAX_CLIPS_PER_SHORT)
        candidate_scenes = [
            (trailer, float(start))
            for trailer in _TRAILERS
            for start in range(12, int(trailer.duration_seconds - self._CLIP_DURATION_SECONDS), 9)
        ]
        selected_scenes = random.sample(candidate_scenes, min(clip_count, len(candidate_scenes)))
        output_dir.mkdir(parents=True, exist_ok=True)

        clips: list[VideoClipAsset] = []
        for index, (trailer, start_seconds) in enumerate(selected_scenes, start=1):
            clip_path = output_dir / f"gta6-trailer-{index:02d}.mp4"
            self._render_trailer_scene(trailer, start_seconds, clip_path)
            clips.append(
                VideoClipAsset(
                    source_id=f"{trailer.name}@{start_seconds:.0f}s",
                    query=topic,
                    source_url=ROCKSTAR_GTA6_MEDIA_PAGE,
                    download_url=trailer.url,
                    local_path=clip_path,
                    duration_seconds=self._CLIP_DURATION_SECONDS,
                    width=1080,
                    height=1920,
                    score=10.0,
                    attribution="Official GTA VI trailer footage, Rockstar Games",
                )
            )

        write_json(
            response_path,
            {
                "source": "Rockstar Games official GTA VI trailers",
                "source_page": ROCKSTAR_GTA6_MEDIA_PAGE,
                "topic": topic,
                "clips": [clip.model_dump(mode="json") for clip in clips],
            },
        )
        return clips

    def _render_trailer_scene(self, trailer: TrailerSource, start_seconds: float, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        filter_graph = (
            "[0:v]scale=1080:-2,setsar=1,split[foreground][background];"
            "[background]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=32:16[bg];"
            "[foreground]scale=1120:630:force_original_aspect_ratio=increase,"
            "crop=1080:607:x='(in_w-out_w)/2+18*sin(t*0.22)':"
            "y='(in_h-out_h)/2+10*cos(t*0.18)'[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[out]"
        )
        run_command(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-ss",
                f"{start_seconds:.2f}",
                "-i",
                trailer.url,
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
                "25",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            timeout_seconds=240,
            stage="stock_video_download",
        )
