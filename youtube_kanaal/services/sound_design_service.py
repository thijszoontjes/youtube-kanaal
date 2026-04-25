from __future__ import annotations

import logging
import random
import shutil
from dataclasses import dataclass
from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.models import SoundDesignAsset
from youtube_kanaal.utils.process import run_command


@dataclass(frozen=True)
class _PlacedStem:
    path: Path
    label: str
    offset_seconds: float
    audio_filter: str | None = None


class SoundDesignService:
    """Build subtle procedural or single-file sound design under narration."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build_mix(
        self,
        *,
        narration_path: Path,
        duration_seconds: float,
        working_dir: Path,
        logger: logging.Logger | None = None,
    ) -> SoundDesignAsset:
        if not self.settings.sound_design_enabled:
            return SoundDesignAsset(
                mixed_path=narration_path,
                applied=False,
                duration_seconds=duration_seconds,
                fallback_reason="Sound design disabled by configuration.",
            )
        if self.settings.mock_mode:
            mix_path = working_dir / "narration_soundscape.wav"
            mix_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(narration_path, mix_path)
            return SoundDesignAsset(
                mixed_path=mix_path,
                applied=False,
                duration_seconds=duration_seconds,
                fallback_reason="Sound design skipped in mock mode.",
            )
        custom_audio_path = self._resolve_custom_audio_path()
        if custom_audio_path is not None:
            return self._build_custom_mix(
                narration_path=narration_path,
                duration_seconds=duration_seconds,
                working_dir=working_dir,
                custom_audio_path=custom_audio_path,
                logger=logger,
            )

        sound_dir = working_dir / "sound_design"
        sound_dir.mkdir(parents=True, exist_ok=True)
        if logger:
            logger.info(
                "sound_design_plan: start",
                extra={
                    "duration_seconds": duration_seconds,
                    "working_dir": str(sound_dir),
                },
            )

        transition_hits = self._transition_offsets(duration_seconds)
        placed_stems: list[_PlacedStem] = []
        stem_paths: list[Path] = []

        room_tone_path = sound_dir / "room_tone.wav"
        self._render_room_tone(room_tone_path, duration_seconds)
        placed_stems.append(_PlacedStem(path=room_tone_path, label="room", offset_seconds=0.0))
        stem_paths.append(room_tone_path)

        for index, offset in enumerate(transition_hits, start=1):
            whoosh_path = sound_dir / f"whoosh_{index:02d}.wav"
            self._render_whoosh(whoosh_path)
            whoosh_offset = max(offset - 0.08, 0.0)
            placed_stems.append(_PlacedStem(path=whoosh_path, label=f"whoosh_{index:02d}", offset_seconds=whoosh_offset))
            stem_paths.append(whoosh_path)

            low_hit_path = sound_dir / f"low_hit_{index:02d}.wav"
            self._render_low_hit(low_hit_path)
            placed_stems.append(_PlacedStem(path=low_hit_path, label=f"low_hit_{index:02d}", offset_seconds=offset))
            stem_paths.append(low_hit_path)

        riser_duration = min(3.0, max(duration_seconds * 0.14, 1.8))
        riser_start = max(duration_seconds - riser_duration - 0.35, 0.6)
        riser_path = sound_dir / "riser.wav"
        self._render_riser(riser_path, riser_duration)
        placed_stems.append(_PlacedStem(path=riser_path, label="riser", offset_seconds=riser_start))
        stem_paths.append(riser_path)

        mix_path = working_dir / "narration_soundscape.wav"
        self._mix_stems(
            narration_path=narration_path,
            output_path=mix_path,
            stems=placed_stems,
        )
        if logger:
            logger.info(
                "sound_design_plan: finish",
                extra={
                    "duration_seconds": duration_seconds,
                    "effect_count": len(placed_stems),
                    "transition_hits": transition_hits,
                    "mix_path": str(mix_path),
                },
            )
        return SoundDesignAsset(
            mixed_path=mix_path,
            applied=True,
            duration_seconds=duration_seconds,
            effect_count=len(placed_stems),
            stem_paths=stem_paths,
        )

    def _resolve_custom_audio_path(self) -> Path | None:
        if not self.settings.sound_design_custom_audio_filename:
            return None
        filename = Path(self.settings.sound_design_custom_audio_filename).name
        return self.settings.sound_design_custom_audio_dir / filename

    def _build_custom_mix(
        self,
        *,
        narration_path: Path,
        duration_seconds: float,
        working_dir: Path,
        custom_audio_path: Path,
        logger: logging.Logger | None = None,
    ) -> SoundDesignAsset:
        if not custom_audio_path.exists():
            return SoundDesignAsset(
                mixed_path=narration_path,
                applied=False,
                duration_seconds=duration_seconds,
                fallback_reason=f"Custom sound design file was not found: {custom_audio_path}",
            )

        sound_dir = working_dir / "sound_design"
        sound_dir.mkdir(parents=True, exist_ok=True)
        offset_seconds = self._random_offset(duration_seconds)
        remaining_seconds = max(duration_seconds - offset_seconds, 0.1)
        placed_stem = _PlacedStem(
            path=custom_audio_path,
            label=custom_audio_path.name,
            offset_seconds=offset_seconds,
            audio_filter=f"atrim=0:{remaining_seconds:.2f},volume=-7dB",
        )
        mix_path = working_dir / "narration_soundscape.wav"
        self._mix_stems(
            narration_path=narration_path,
            output_path=mix_path,
            stems=[placed_stem],
        )
        if logger:
            logger.info(
                "sound_design_plan: custom_single_random",
                extra={
                    "duration_seconds": duration_seconds,
                    "custom_audio_path": str(custom_audio_path),
                    "offset_seconds": offset_seconds,
                    "mix_path": str(mix_path),
                },
            )
        return SoundDesignAsset(
            mixed_path=mix_path,
            applied=True,
            duration_seconds=duration_seconds,
            effect_count=1,
            stem_paths=[custom_audio_path],
        )

    def _transition_offsets(self, duration_seconds: float) -> list[float]:
        anchors = [0.16]
        anchors.extend(
            offset
            for offset in (
                round(duration_seconds * 0.34, 2),
                round(duration_seconds * 0.66, 2),
            )
            if 0.45 < offset < max(duration_seconds - 2.8, 0.45)
        )
        deduped: list[float] = []
        for offset in anchors:
            if offset not in deduped:
                deduped.append(offset)
        return deduped

    def _random_offset(self, duration_seconds: float) -> float:
        if duration_seconds <= 0.15:
            return 0.0
        return round(random.uniform(0.0, max(duration_seconds - 0.15, 0.0)), 2)

    def _render_room_tone(self, output_path: Path, duration_seconds: float) -> None:
        self._render_effect(
            output_path=output_path,
            source_filter=f"anoisesrc=color=pink:amplitude=0.02:sample_rate=48000:d={duration_seconds:.2f}",
            audio_filter=(
                "highpass=f=120,"
                "lowpass=f=1800,"
                "volume=-40dB,"
                "afade=t=in:st=0:d=0.7,"
                f"afade=t=out:st={max(duration_seconds - 0.8, 0):.2f}:d=0.8"
            ),
        )

    def _render_whoosh(self, output_path: Path) -> None:
        self._render_effect(
            output_path=output_path,
            source_filter="anoisesrc=color=white:amplitude=0.28:sample_rate=48000:d=0.45",
            audio_filter=(
                "highpass=f=700,"
                "lowpass=f=8500,"
                "volume=-27dB,"
                "afade=t=in:st=0:d=0.03,"
                "afade=t=out:st=0.18:d=0.27"
            ),
        )

    def _render_low_hit(self, output_path: Path) -> None:
        self._render_effect(
            output_path=output_path,
            source_filter="sine=frequency=58:sample_rate=48000:duration=0.35",
            audio_filter=(
                "lowpass=f=110,"
                "volume=-28dB,"
                "afade=t=in:st=0:d=0.01,"
                "afade=t=out:st=0.10:d=0.25"
            ),
        )

    def _render_riser(self, output_path: Path, duration_seconds: float) -> None:
        fade_out_start = max(duration_seconds - 0.45, 0.0)
        self._render_effect(
            output_path=output_path,
            source_filter=f"anoisesrc=color=white:amplitude=0.16:sample_rate=48000:d={duration_seconds:.2f}",
            audio_filter=(
                "highpass=f=1400,"
                "lowpass=f=7000,"
                "volume=-31dB,"
                f"afade=t=in:st=0:d={max(duration_seconds - 0.7, 0.6):.2f},"
                f"afade=t=out:st={fade_out_start:.2f}:d=0.45"
            ),
        )

    def _render_effect(self, *, output_path: Path, source_filter: str, audio_filter: str) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-f",
                "lavfi",
                "-i",
                source_filter,
                "-af",
                audio_filter,
                "-ar",
                "48000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ],
            timeout_seconds=180,
            stage="sound_design",
        )

    def _mix_stems(
        self,
        *,
        narration_path: Path,
        output_path: Path,
        stems: list[_PlacedStem],
    ) -> None:
        command = [self.settings.ffmpeg_binary, "-y", "-i", str(narration_path)]
        filter_parts = ["[0:a]aformat=sample_rates=48000:channel_layouts=mono[voice]"]
        mix_labels = ["[voice]"]

        for index, stem in enumerate(stems, start=1):
            command.extend(["-i", str(stem.path)])
            label = f"fx{index}"
            delay_ms = max(int(round(stem.offset_seconds * 1000)), 0)
            stem_filters = [f"[{index}:a]aformat=sample_rates=48000:channel_layouts=mono"]
            if stem.audio_filter:
                stem_filters.append(stem.audio_filter)
            stem_filters.append(f"adelay={delay_ms}:all=1[{label}]")
            filter_parts.append(",".join(stem_filters))
            mix_labels.append(f"[{label}]")

        filter_parts.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:normalize=0:dropout_transition=0,"
            "alimiter=limit=0.92[out]"
        )
        command.extend(
            [
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                "[out]",
                "-ar",
                "48000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ]
        )
        run_command(
            command,
            timeout_seconds=300,
            stage="sound_design",
        )
