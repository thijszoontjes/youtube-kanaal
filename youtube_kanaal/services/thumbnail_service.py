from __future__ import annotations

from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError


class ThumbnailService:
    """Generate a consistent 1280x720 thumbnail with free local tooling."""

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
        image = self._load_background(background_path)
        image = image.resize((1280, 720)).filter(ImageFilter.GaussianBlur(radius=1.2))
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rectangle((0, 0, 1280, 720), fill=(0, 0, 0, 104))
        draw.rectangle((0, 0, 560, 720), fill=(0, 0, 0, 142))
        draw.rectangle((44, 546, 1238, 618), fill=self._hex_rgba(self.settings.thumbnail_accent_color, 238))
        draw.polygon([(958, 0), (1280, 0), (1280, 118), (1012, 86)], fill=(255, 216, 38, 238))
        image = Image.alpha_composite(image.convert("RGBA"), overlay)

        draw = ImageDraw.Draw(image)
        main_text = self._compact_text(title_text or topic)
        topic_text = self._compact_text(topic).upper()
        badge_font = self._font(ImageFont, 42, bold=True)
        title_font = self._font(ImageFont, 116, bold=True)
        topic_font = self._font(ImageFont, 42, bold=True)
        small_font = self._font(ImageFont, 34, bold=True)

        draw.rectangle((54, 50, 294, 104), fill=(255, 216, 38))
        draw.text((72, 57), "WAIT...", font=badge_font, fill="#080808")
        draw.text((986, 18), "DO NOT\nMISS", font=badge_font, fill="#080808", spacing=0)

        lines = self._wrap_lines(draw, main_text, title_font, max_width=850, max_lines=3)
        y = 126
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=title_font, stroke_width=4)
            draw.text(
                (56, y),
                line,
                font=title_font,
                fill=self.settings.thumbnail_text_color,
                stroke_width=4,
                stroke_fill="#000000",
            )
            y += (bbox[3] - bbox[1]) + 4

        draw.text((58, 558), topic_text[:34], font=topic_font, fill="#050505")
        draw.text((58, 632), "NOT WHAT YOU THINK", font=small_font, fill="#FFFFFF")
        image.convert("RGB").save(output_path, quality=92)
        return output_path

    def _load_background(self, background_path: Path | None):
        from PIL import Image

        if background_path and background_path.exists() and background_path.stat().st_size > 0:
            try:
                return Image.open(background_path).convert("RGB")
            except OSError:
                pass
        return Image.new("RGB", (1280, 720), (18, 22, 24))

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
        words = text.upper().split()
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
        return lines or [text.upper()[:20]]

    def _compact_text(self, value: str) -> str:
        cleaned = " ".join(value.replace(":", " ").replace("|", " ").split())
        return cleaned[:38].strip()

    def _hex_rgba(self, value: str, alpha: int) -> tuple[int, int, int, int]:
        cleaned = value.strip().lstrip("#")
        if len(cleaned) != 6:
            return (107, 255, 124, alpha)
        return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16), alpha)
