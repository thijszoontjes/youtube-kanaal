from __future__ import annotations

import hashlib
from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError

WIDTH = 1920
HEIGHT = 1080


class ThumbnailService:
    """Generate clean 1920x1080 thumbnails from the video's own visual frame."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate(
        self,
        *,
        title_text: str,
        topic: str,
        background_path: Path | None,
        output_path: Path,
    ) -> Path:
        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageFont
        except ImportError as exc:
            raise PipelineStageError(
                stage="thumbnail_generation",
                message="Pillow is required for thumbnail generation.",
                probable_cause="Install dependencies with `pip install -e .`.",
            ) from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        seed = self._seed(title_text, topic)
        base = self._prepare_background(self._load_background(background_path), Image, ImageFilter)
        main_text = self._compact_text(title_text or topic, fallback=topic, max_chars=34)
        topic_text = self._compact_text(topic, fallback="WATCH THIS", max_chars=30)
        layout = seed % 4

        if layout == 0:
            image = self._render_side_panel(base, main_text, topic_text, Image, ImageDraw, ImageFont, seed, side="left")
        elif layout == 1:
            image = self._render_side_panel(base, main_text, topic_text, Image, ImageDraw, ImageFont, seed, side="right")
        elif layout == 2:
            image = self._render_lower_banner(base, main_text, topic_text, Image, ImageDraw, ImageFont, seed)
        else:
            image = self._render_center_card(base, main_text, topic_text, Image, ImageDraw, ImageFont, seed)

        image.convert("RGB").save(output_path, quality=96, subsampling=0, optimize=True)
        return output_path

    def _prepare_background(self, image, image_module, image_filter_module):
        from PIL import ImageEnhance, ImageOps

        image = ImageOps.fit(image, (WIDTH, HEIGHT), method=image_module.Resampling.LANCZOS, centering=(0.5, 0.5))
        image = ImageEnhance.Color(image).enhance(1.14)
        image = ImageEnhance.Contrast(image).enhance(1.16)
        image = ImageEnhance.Sharpness(image).enhance(1.55)
        return image.filter(image_filter_module.UnsharpMask(radius=1.1, percent=135, threshold=3)).convert("RGBA")

    def _render_side_panel(
        self,
        base,
        main_text: str,
        topic: str,
        image_module,
        draw_module,
        font_module,
        seed: int,
        *,
        side: str,
    ):
        image = base.copy()
        overlay = image_module.new("RGBA", image.size, (0, 0, 0, 0))
        draw = draw_module.Draw(overlay)
        panel = (0, 0, 870, HEIGHT) if side == "left" else (1050, 0, WIDTH, HEIGHT)
        fade = (820, 0, 1050, HEIGHT) if side == "left" else (870, 0, 1100, HEIGHT)
        draw.rectangle(panel, fill=(5, 7, 10, 212))
        draw.rectangle(fade, fill=(5, 7, 10, 72))
        image = image_module.alpha_composite(image, overlay)

        draw = draw_module.Draw(image)
        palette = self._palette(seed)
        x1 = 84 if side == "left" else 1125
        x2 = 800 if side == "left" else 1835
        self._draw_tag(draw, (x1, 92), self._hook_label(seed), font_module, palette["red"])
        self._draw_fitted_text(draw, main_text, (x1, 218, x2, 710), font_module, max_lines=3, fill="#FFFFFF")
        self._draw_topic_bar(draw, topic, (x1, 790), font_module, palette)
        return image

    def _render_lower_banner(self, base, main_text: str, topic: str, image_module, draw_module, font_module, seed: int):
        image = base.copy()
        overlay = image_module.new("RGBA", image.size, (0, 0, 0, 0))
        draw = draw_module.Draw(overlay)
        draw.rectangle((0, 0, WIDTH, HEIGHT), fill=(0, 0, 0, 34))
        draw.rectangle((0, 635, WIDTH, HEIGHT), fill=(5, 7, 10, 202))
        image = image_module.alpha_composite(image, overlay)

        draw = draw_module.Draw(image)
        palette = self._palette(seed)
        self._draw_tag(draw, (88, 93), self._hook_label(seed), font_module, palette["red"])
        self._draw_fitted_text(draw, main_text, (86, 680, 1450, 980), font_module, max_lines=2, fill="#FFFFFF")
        self._draw_topic_bar(draw, topic, (88, 565), font_module, palette)
        return image

    def _render_center_card(self, base, main_text: str, topic: str, image_module, draw_module, font_module, seed: int):
        image = base.copy()
        overlay = image_module.new("RGBA", image.size, (0, 0, 0, 0))
        draw = draw_module.Draw(overlay)
        draw.rectangle((0, 0, WIDTH, HEIGHT), fill=(0, 0, 0, 74))
        draw.rounded_rectangle((110, 118, 1125, 914), radius=18, fill=(5, 7, 10, 188))
        image = image_module.alpha_composite(image, overlay)

        draw = draw_module.Draw(image)
        palette = self._palette(seed)
        self._draw_tag(draw, (158, 172), self._hook_label(seed), font_module, palette["red"])
        self._draw_fitted_text(draw, main_text, (158, 308, 1060, 725), font_module, max_lines=3, fill="#FFFFFF")
        self._draw_topic_bar(draw, topic, (158, 784), font_module, palette)
        return image

    def _load_background(self, background_path: Path | None):
        from PIL import Image

        if background_path and background_path.exists() and background_path.stat().st_size > 0:
            try:
                return Image.open(background_path).convert("RGB")
            except OSError:
                pass
        return Image.new("RGB", (WIDTH, HEIGHT), (18, 22, 24))

    def _font(self, image_font_module, size: int, *, bold: bool):
        candidates: list[Path] = []
        if self.settings.thumbnail_font_path:
            candidates.append(self.settings.thumbnail_font_path)
        candidates.extend(
            [
                Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
                Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
                Path("/System/Library/Fonts/Supplemental/Arial Black.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf"),
                Path("/System/Library/Fonts/Supplemental/Impact.ttf"),
                Path("/System/Library/Fonts/HelveticaNeue.ttc"),
                Path("/Library/Fonts/Arial Unicode.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
            ]
        )
        for candidate in candidates:
            if candidate.exists():
                return image_font_module.truetype(str(candidate), size=size)
        return image_font_module.load_default()

    def _draw_tag(self, draw, xy: tuple[int, int], text: str, font_module, fill: str) -> None:
        font = self._font(font_module, 56, bold=True)
        x, y = xy
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.rounded_rectangle((x, y, x + bbox[2] + 48, y + 78), radius=10, fill=fill)
        draw.text((x + 24, y + 8), text, font=font, fill="#FFFFFF")

    def _draw_topic_bar(self, draw, topic: str, xy: tuple[int, int], font_module, palette: dict[str, str]) -> None:
        font = self._font(font_module, 52, bold=True)
        label = topic.upper()
        x, y = xy
        bbox = draw.textbbox((0, 0), label, font=font)
        width = min(820, bbox[2] + 56)
        draw.rounded_rectangle((x, y, x + width, y + 82), radius=8, fill=palette["yellow"])
        draw.text((x + 28, y + 11), label, font=font, fill="#060606")

    def _draw_fitted_text(
        self,
        draw,
        text: str,
        box: tuple[int, int, int, int],
        font_module,
        *,
        max_lines: int,
        fill: str,
    ) -> None:
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1
        best_font = self._font(font_module, 82, bold=True)
        best_lines = [text.upper()]
        for size in range(188, 78, -4):
            font = self._font(font_module, size, bold=True)
            lines = self._wrap_lines(draw, text, font, max_width=width, max_lines=max_lines)
            line_boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=8) for line in lines]
            total_height = sum(line_box[3] - line_box[1] for line_box in line_boxes)
            total_height += max(0, len(lines) - 1) * max(4, size // 16)
            widest = max((line_box[2] - line_box[0] for line_box in line_boxes), default=0)
            if total_height <= height and widest <= width:
                best_font = font
                best_lines = lines
                break

        line_gap = max(4, best_font.size // 16) if hasattr(best_font, "size") else 6
        line_boxes = [draw.textbbox((0, 0), line, font=best_font, stroke_width=8) for line in best_lines]
        total_height = sum(line_box[3] - line_box[1] for line_box in line_boxes)
        total_height += max(0, len(best_lines) - 1) * line_gap
        y = y1 + max(0, (height - total_height) // 2)
        for line, bbox in zip(best_lines, line_boxes):
            draw.text(
                (x1, y),
                line,
                font=best_font,
                fill=fill,
                stroke_width=8,
                stroke_fill="#050505",
            )
            y += (bbox[3] - bbox[1]) + line_gap

    def _wrap_lines(self, draw, text: str, font, *, max_width: int, max_lines: int) -> list[str]:
        words = text.upper().split()
        lines: list[str] = []
        current: list[str] = []
        index = 0
        while index < len(words):
            word = words[index]
            candidate = " ".join([*current, word])
            width = draw.textbbox((0, 0), candidate, font=font, stroke_width=8)[2]
            if current and width > max_width:
                if len(lines) < max_lines - 1:
                    lines.append(" ".join(current))
                    current = [word]
                else:
                    current = [*current, *words[index:]]
                    break
            else:
                current.append(word)
            index += 1
        if current and len(lines) < max_lines:
            lines.append(" ".join(current))
        return lines or [text.upper()[:20]]

    def _compact_text(self, value: str, *, fallback: str, max_chars: int) -> str:
        cleaned = " ".join(value.replace(":", " ").replace("|", " ").split()).strip()
        if not cleaned:
            cleaned = fallback
        cleaned = cleaned.upper()
        if len(cleaned) <= max_chars:
            return cleaned
        words: list[str] = []
        for word in cleaned.split():
            candidate = " ".join([*words, word])
            if len(candidate) > max_chars:
                break
            words.append(word)
        return (" ".join(words) or cleaned[:max_chars]).strip()

    def _hook_label(self, seed: int) -> str:
        labels = ["EXPOSED", "WAIT...", "HIDDEN", "SHOCKING"]
        return labels[(seed // 7) % len(labels)]

    def _seed(self, title_text: str, topic: str) -> int:
        digest = hashlib.sha256(f"{topic}|{title_text}".encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    def _palette(self, seed: int) -> dict[str, str]:
        palettes = [
            {"red": "#EF233C", "yellow": "#FFD400"},
            {"red": "#D90429", "yellow": "#FFFFFF"},
            {"red": "#111111", "yellow": "#FFDD33"},
            {"red": "#FF3B30", "yellow": "#EAF2FF"},
        ]
        return palettes[seed % len(palettes)]
