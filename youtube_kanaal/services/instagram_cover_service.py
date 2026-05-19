from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.utils.process import run_command


class InstagramCoverService:
    """Create Reel feed covers and prepend them to Instagram-only upload videos."""

    _PALETTES = [
        ((8, 15, 28), (0, 213, 255), (116, 255, 126), (255, 238, 84)),
        ((14, 18, 31), (255, 73, 116), (55, 215, 255), (255, 215, 91)),
        ((11, 22, 19), (96, 255, 183), (255, 196, 74), (77, 166, 255)),
        ((16, 15, 27), (178, 125, 255), (69, 239, 211), (255, 238, 117)),
    ]

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate_cover(self, *, title: str, topic: str, output_path: Path) -> Path:
        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageFont
        except ImportError as exc:
            raise PipelineStageError(
                stage="instagram_cover",
                message="Pillow is required for Instagram cover generation.",
                probable_cause="Install dependencies with `pip install -e .`.",
            ) from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        palette = self._palette_for(f"{topic}|{title}")
        bg, accent, accent_alt, warm = palette
        image = self._gradient_image(Image, bg, accent_alt)

        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse((-190, 120, 460, 770), fill=(*accent, 92))
        glow_draw.ellipse((700, 870, 1360, 1570), fill=(*accent_alt, 78))
        glow_draw.ellipse((220, 1440, 900, 2110), fill=(*warm, 52))
        image = Image.alpha_composite(image.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(radius=58)))

        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for x in range(-240, 1320, 96):
            draw.line((x, 0, x + 650, 1920), fill=(255, 255, 255, 18), width=2)
        for y in range(180, 1800, 160):
            draw.line((0, y, 1080, y - 90), fill=(*accent, 21), width=3)
        draw.rectangle((0, 0, 1080, 1920), fill=(0, 0, 0, 62))
        draw.rounded_rectangle((82, 462, 998, 1378), radius=54, fill=(0, 0, 0, 116), outline=(*accent, 178), width=4)
        draw.rectangle((82, 462, 998, 536), fill=(*accent, 228))
        draw.rectangle((82, 1302, 998, 1378), fill=(*accent_alt, 218))
        image = Image.alpha_composite(image, overlay)

        draw = ImageDraw.Draw(image)
        badge_font = self._font(ImageFont, 42, bold=True)
        title_font = self._fit_title_font(ImageFont, draw, title)
        topic_font = self._font(ImageFont, 42, bold=True)
        label_font = self._font(ImageFont, 34, bold=True)

        draw.text((118, 477), "DID YOU KNOW?", font=badge_font, fill=(6, 11, 18))
        draw.text((118, 1320), self._compact(topic).upper()[:30], font=topic_font, fill=(7, 12, 19))
        draw.text((118, 1422), "FAST FACTS IN UNDER A MINUTE", font=label_font, fill=(238, 244, 250))

        lines = self._wrap_lines(draw, self._compact(title).upper(), title_font, max_width=820, max_lines=4)
        line_heights = [
            draw.textbbox((0, 0), line, font=title_font, stroke_width=3)[3]
            - draw.textbbox((0, 0), line, font=title_font, stroke_width=3)[1]
            for line in lines
        ]
        spacing = 14
        total_height = sum(line_heights) + spacing * max(len(lines) - 1, 0)
        title_area_top = 590
        title_area_bottom = 1188
        y = max(title_area_top, title_area_top + ((title_area_bottom - title_area_top) - total_height) // 2)
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=title_font, stroke_width=3)
            line_width = bbox[2] - bbox[0]
            draw.text(
                ((1080 - line_width) / 2, y),
                line,
                font=title_font,
                fill=(255, 255, 255),
                stroke_width=3,
                stroke_fill=(0, 0, 0),
            )
            y += (bbox[3] - bbox[1]) + 14

        image.convert("RGB").save(output_path, quality=93)
        return output_path

    def build_reel_with_cover(
        self,
        *,
        video_path: Path,
        cover_path: Path,
        output_path: Path,
        cover_duration_seconds: float = 0.85,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.mock_mode:
            shutil.copy2(video_path, output_path)
            return output_path

        filter_graph = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1,fps=30,format=yuv420p[cover];"
            "[1:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1,fps=30,format=yuv420p[main];"
            "[cover][main]concat=n=2:v=1:a=0[v];"
            "[1:a]aformat=sample_rates=48000:channel_layouts=mono[maina];"
            "[2:a][maina]concat=n=2:v=0:a=1[a]"
        )
        run_command(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-loop",
                "1",
                "-t",
                f"{cover_duration_seconds:.2f}",
                "-i",
                str(cover_path),
                "-i",
                str(video_path),
                "-f",
                "lavfi",
                "-t",
                f"{cover_duration_seconds:.2f}",
                "-i",
                "anullsrc=channel_layout=mono:sample_rate=48000",
                "-filter_complex",
                filter_graph,
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-threads",
                "2",
                "-preset",
                "medium",
                "-crf",
                "22",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            timeout_seconds=600,
            stage="instagram_cover",
        )
        return output_path

    def _gradient_image(self, image_module, base: tuple[int, int, int], accent: tuple[int, int, int]):
        from PIL import ImageDraw

        width, height = 1080, 1920
        image = image_module.new("RGB", (width, height), base)
        draw = ImageDraw.Draw(image)
        for y in range(height):
            mix = y / max(height - 1, 1)
            amount = min(0.42, mix * 0.42)
            color = tuple(int(base[i] * (1 - amount) + accent[i] * amount) for i in range(3))
            draw.line((0, y, width, y), fill=color)
        return image

    def _fit_title_font(self, image_font_module, draw, title: str):
        title_area_height = 598
        for size in (104, 96, 88, 80, 72, 66, 60):
            font = self._font(image_font_module, size, bold=True)
            lines = self._wrap_lines(draw, self._compact(title).upper(), font, max_width=820, max_lines=4)
            heights = [
                draw.textbbox((0, 0), line, font=font, stroke_width=3)[3]
                - draw.textbbox((0, 0), line, font=font, stroke_width=3)[1]
                for line in lines
            ]
            total_height = sum(heights) + 14 * max(len(lines) - 1, 0)
            if (
                len(lines) <= 4
                and total_height <= title_area_height
                and all(draw.textbbox((0, 0), line, font=font, stroke_width=3)[2] <= 820 for line in lines)
            ):
                return font
        return self._font(image_font_module, 58, bold=True)

    def _font(self, image_font_module, size: int, *, bold: bool):
        candidates: list[Path] = []
        if self.settings.thumbnail_font_path:
            candidates.append(self.settings.thumbnail_font_path)
        candidates.extend(
            [
                Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
                Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
            ]
        )
        for candidate in candidates:
            if candidate.exists():
                return image_font_module.truetype(str(candidate), size=size)
        return image_font_module.load_default()

    def _wrap_lines(self, draw, text: str, font, *, max_width: int, max_lines: int) -> list[str]:
        words = text.split()
        lines: list[str] = []
        current: list[str] = []
        for word in words:
            candidate = " ".join([*current, word])
            width = draw.textbbox((0, 0), candidate, font=font, stroke_width=3)[2]
            if current and width > max_width:
                lines.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(" ".join(current))
        return lines or [text[:22]]

    def _compact(self, value: str) -> str:
        return " ".join(value.replace("|", " ").replace(":", " ").split()).strip()[:62]

    def _palette_for(self, seed: str):
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        return self._PALETTES[digest[0] % len(self._PALETTES)]
