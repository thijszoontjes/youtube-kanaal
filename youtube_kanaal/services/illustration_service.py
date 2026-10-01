from __future__ import annotations

from math import atan2, cos, sin
from pathlib import Path

from youtube_kanaal.models.assets import ImageAsset
from youtube_kanaal.models.content import LongIllustrationPlan, LongVideoSection


class IllustrationService:
    """Draw small, original explainer diagrams as dependency-free PPM assets."""

    WIDTH = 1280
    HEIGHT = 720
    INK = (36, 47, 58)
    PAPER = (250, 249, 245)
    TEAL = (49, 147, 132)
    CORAL = (220, 103, 83)
    GOLD = (225, 176, 76)
    BLUE = (88, 132, 175)
    LIGHT_TEAL = (206, 232, 225)
    LIGHT_BLUE = (218, 230, 242)

    BRIDGE_VARIANTS = ("beam", "arch", "truss", "suspension", "cable-stayed", "cantilever")

    def __init__(self, *, width: int = WIDTH, height: int = HEIGHT) -> None:
        self.width = width
        self.height = height

    def create_chapter_scenes(
        self,
        *,
        section: LongVideoSection,
        topic: str,
        output_dir: Path,
        chapter_index: int,
    ) -> tuple[list[ImageAsset], LongIllustrationPlan]:
        output_dir.mkdir(parents=True, exist_ok=True)
        plan = self._normalized_plan(section.visual_plan, section, topic, chapter_index)
        assets: list[ImageAsset] = []
        for phase in ("structure", "load_path"):
            path = output_dir / f"illustration-chapter-{chapter_index:02d}-{phase}.ppm"
            pixels = bytearray(self.width * self.height * 3)
            self._fill(pixels, self.PAPER)
            if plan.kind == "bridge":
                variant = self._bridge_variant(plan.variant, section.title, chapter_index)
                self._draw_bridge(pixels, variant, phase)
            else:
                variant = plan.variant or plan.kind
                self._draw_concept(pixels, plan, phase)
            self._write_ppm(path, pixels)
            assets.append(
                ImageAsset(
                    source_id=f"illustration-ch{chapter_index:02d}-{phase}",
                    query=section.title,
                    local_path=path,
                    width=self.width,
                    height=self.height,
                    rights_status="generated_local",
                )
            )
        return assets, plan.model_copy(update={"variant": variant})

    def _normalized_plan(
        self,
        plan: LongIllustrationPlan,
        section: LongVideoSection,
        topic: str,
        index: int,
    ) -> LongIllustrationPlan:
        is_bridge = "bridge" in topic.casefold() or plan.kind == "bridge"
        kind = "bridge" if is_bridge else plan.kind
        elements = plan.elements[:]
        variant = plan.variant
        if is_bridge:
            variant = self._bridge_variant(variant, section.title, index)
            if len(elements) < 2 or not plan.relation:
                raise ValueError(f"Bridge illustration plan for {section.title!r} needs labeled elements and a relation.")
        elif len(elements) < 2 or not plan.relation:
            raise ValueError(f"Illustration plan for {section.title!r} is incomplete; no generic fallback is available.")
        relation = plan.relation
        return LongIllustrationPlan(kind=kind, variant=variant, elements=elements[:5], relation=relation)

    def _bridge_variant(self, requested: str, title: str, index: int) -> str:
        value = f"{requested} {title}".casefold()
        for variant in self.BRIDGE_VARIANTS:
            if variant in value:
                return variant
        if "cable stay" in value or "cable-stayed" in value:
            return "cable-stayed"
        return self.BRIDGE_VARIANTS[(index - 1) % len(self.BRIDGE_VARIANTS)]

    def _draw_bridge(self, pixels: bytearray, variant: str, phase: str) -> None:
        w, h = self.width, self.height
        deck_y = int(h * 0.61)
        x0, x1 = int(w * 0.16), int(w * 0.84)
        deck_color = self.CORAL if phase == "load_path" and variant == "beam" else self.INK
        self._line(pixels, x0, deck_y, x1, deck_y, deck_color, 16)
        self._line(pixels, x0, deck_y + 20, x1, deck_y + 20, self.TEAL, 5)
        self._line(pixels, int(w * .10), deck_y + 86, int(w * .90), deck_y + 86, self.INK, 4)

        if variant == "beam":
            self._rect(pixels, int(w * .29), deck_y + 22, int(w * .36), h - 74, self.LIGHT_BLUE, self.INK, 8)
            self._rect(pixels, int(w * .63), deck_y + 22, int(w * .70), h - 74, self.LIGHT_BLUE, self.INK, 8)
            supports = [(int(w * .32), deck_y), (int(w * .67), deck_y)]
        elif variant == "arch":
            points = []
            for step in range(41):
                t = step / 40
                x = int(w * (.18 + .64 * t))
                y = int(deck_y + 140 * (2 * t - 1) ** 2 - 20)
                points.append((x, y))
            self._polyline(pixels, points, self.CORAL if phase == "load_path" else self.INK, 13)
            for step in range(0, 41, 5):
                x, y = points[step]
                self._line(pixels, x, deck_y + 4, x, y, self.BLUE, 5)
            self._rect(pixels, x0 - 12, deck_y + 38, x0 + 12, deck_y + 96, self.GOLD, self.INK, 5)
            self._rect(pixels, x1 - 12, deck_y + 38, x1 + 12, deck_y + 96, self.GOLD, self.INK, 5)
            supports = [(x0, deck_y), (x1, deck_y)]
        elif variant == "truss":
            top_y = deck_y - int(h * .20)
            self._line(pixels, x0, top_y, x1, top_y, self.BLUE, 10)
            self._line(pixels, x0, deck_y - 4, x1, deck_y - 4, self.BLUE, 8)
            points = [(x0 + (x1 - x0) * i // 10, deck_y - 4 if i % 2 == 0 else top_y) for i in range(11)]
            for i in range(10):
                self._line(pixels, *points[i], *points[i + 1], self.CORAL if phase == "load_path" else self.INK, 7)
                mid_x = (points[i][0] + points[i + 1][0]) // 2
                self._line(pixels, mid_x, top_y, mid_x, deck_y - 4, self.INK, 5)
            supports = [(int(w * .30), deck_y), (int(w * .70), deck_y)]
        elif variant == "suspension":
            tower_xs = (int(w * .34), int(w * .66))
            for x in tower_xs:
                self._rect(pixels, x - 18, int(h * .24), x + 18, deck_y, self.LIGHT_BLUE, self.INK, 7)
            cable = []
            for step in range(41):
                t = step / 40
                x = int(x0 + (x1 - x0) * t)
                y = int(h * .28 + 130 * (1 - (2 * t - 1) ** 2))
                cable.append((x, y))
            self._polyline(pixels, cable, self.CORAL if phase == "load_path" else self.INK, 9)
            for x, cable_y in cable[::3]:
                self._line(pixels, x, cable_y, x, deck_y, self.INK, 3)
            supports = [(x, deck_y) for x in tower_xs]
        elif variant == "cable-stayed":
            tower_xs = (int(w * .43), int(w * .62))
            for x in tower_xs:
                self._rect(pixels, x - 17, int(h * .22), x + 17, deck_y, self.LIGHT_BLUE, self.INK, 7)
                for attach_x in range(x0 + 40, x1 - 20, 105):
                    if (attach_x < x and x == tower_xs[0]) or (attach_x > x and x == tower_xs[1]):
                        self._line(pixels, x, int(h * .26), attach_x, deck_y, self.CORAL if phase == "load_path" else self.BLUE, 5)
            supports = [(x, deck_y) for x in tower_xs]
        else:  # cantilever
            left, right = int(w * .35), int(w * .65)
            for x in (left, right):
                self._rect(pixels, x - 20, deck_y + 22, x + 20, h - 74, self.GOLD, self.INK, 6)
            self._line(pixels, x0, deck_y - 2, left + 110, deck_y - 2, self.BLUE, 15)
            self._line(pixels, right - 110, deck_y - 2, x1, deck_y - 2, self.BLUE, 15)
            self._line(pixels, left + 110, deck_y - 18, right - 110, deck_y - 18, self.CORAL, 12)
            supports = [(left, deck_y), (right, deck_y)]

        if variant not in {"arch", "suspension", "cable-stayed", "cantilever"}:
            for x, y in supports:
                self._rect(pixels, x - 28, y + 22, x + 28, h - 74, self.LIGHT_TEAL, self.INK, 6)
        if phase == "load_path":
            for x, _ in supports:
                self._arrow(pixels, x, deck_y - 100, x, deck_y + 52, self.CORAL, 9)
            self._arrow(pixels, int(w * .5), int(h * .35), int(w * .5), deck_y - 24, self.GOLD, 7)

    def _draw_concept(self, pixels: bytearray, plan: LongIllustrationPlan, phase: str) -> None:
        count = max(2, min(5, len(plan.elements)))
        colors = (self.LIGHT_BLUE, self.LIGHT_TEAL, (245, 227, 190), (238, 214, 209), (221, 229, 207))
        if plan.kind == "timeline":
            left, right = int(self.width * .16), int(self.width * .84)
            cy = int(self.height * .56)
            self._line(pixels, left, cy, right, cy, self.INK, 10)
            for index in range(count):
                x = left + (right - left) * index // (count - 1)
                color = self.CORAL if phase == "load_path" and index == count - 1 else colors[index % len(colors)]
                self._circle(pixels, x, cy, 25, self.INK)
                self._circle(pixels, x, cy, 18, color)
                self._line(pixels, x, cy - 48, x, cy - 92, self.BLUE, 6)
            return
        if plan.kind == "comparison":
            margin = int(self.width * .15)
            gap = int(self.width * .09)
            panel_w = (self.width - 2 * margin - gap) // 2
            panel_top, panel_bottom = int(self.height * .34), int(self.height * .70)
            for index, x in enumerate((margin, margin + panel_w + gap)):
                fill = self.LIGHT_BLUE if index == 0 else self.LIGHT_TEAL
                if phase == "load_path" and index == 1:
                    fill = (244, 216, 208)
                self._rect(pixels, x, panel_top, x + panel_w, panel_bottom, fill, self.INK, 9)
                self._line(pixels, x + 34, panel_top + 58, x + panel_w - 34, panel_top + 58, self.BLUE, 8)
                self._line(pixels, x + 34, panel_bottom - 58, x + panel_w - 34, panel_bottom - 58, self.CORAL, 8)
            self._line(pixels, self.width // 2, panel_top - 30, self.width // 2, panel_bottom + 30, self.GOLD, 10)
            return

        card_w = int(self.width * .15)
        gap = int(self.width * .035)
        total = count * card_w + (count - 1) * gap
        left = (self.width - total) // 2
        cy = int(self.height * .52)
        xs = [left + i * (card_w + gap) for i in range(count)]
        for index, x in enumerate(xs):
            fill = colors[index % len(colors)]
            if phase == "load_path" and index == count - 1:
                fill = (244, 216, 208)
            self._rect(pixels, x, cy - 72, x + card_w, cy + 72, fill, self.INK, 7)
            node_x = x + card_w // 2
            self._circle(pixels, node_x, cy - 112, 15, self.CORAL if phase == "load_path" and index == count - 1 else self.TEAL)
            self._line(pixels, node_x, cy - 96, node_x, cy - 72, self.INK, 4)
            if index < count - 1:
                color = self.CORAL if phase == "load_path" else self.INK
                self._arrow(pixels, x + card_w + 8, cy, xs[index + 1] - 8, cy, color, 6)
        if phase == "load_path":
            self._rect(pixels, xs[0] - 12, cy - 84, xs[-1] + card_w + 12, cy + 84, self.CORAL, self.CORAL, 5, outline_only=True)

    def _fill(self, pixels: bytearray, color: tuple[int, int, int]) -> None:
        pixels[:] = bytes(color) * (self.width * self.height)

    def _put(self, pixels: bytearray, x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 3
            pixels[offset:offset + 3] = bytes(color)

    def _line(self, pixels: bytearray, x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int], thickness: int = 1) -> None:
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        sx, sy = (1 if x1 < x2 else -1), (1 if y1 < y2 else -1)
        error = dx - dy
        while True:
            radius = max(0, thickness // 2)
            for oy in range(-radius, radius + 1):
                for ox in range(-radius, radius + 1):
                    self._put(pixels, x1 + ox, y1 + oy, color)
            if x1 == x2 and y1 == y2:
                break
            twice = 2 * error
            if twice > -dy:
                error -= dy
                x1 += sx
            if twice < dx:
                error += dx
                y1 += sy

    def _polyline(self, pixels: bytearray, points: list[tuple[int, int]], color: tuple[int, int, int], thickness: int = 1) -> None:
        for first, second in zip(points, points[1:]):
            self._line(pixels, *first, *second, color, thickness)

    def _rect(
        self,
        pixels: bytearray,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        fill: tuple[int, int, int],
        outline: tuple[int, int, int],
        thickness: int = 1,
        *,
        outline_only: bool = False,
    ) -> None:
        left, right = sorted((max(0, x1), min(self.width - 1, x2)))
        top, bottom = sorted((max(0, y1), min(self.height - 1, y2)))
        if not outline_only:
            row = bytes(fill) * (right - left + 1)
            for y in range(top, bottom + 1):
                offset = (y * self.width + left) * 3
                pixels[offset:offset + len(row)] = row
        for edge in range(thickness):
            self._line(pixels, left, top + edge, right, top + edge, outline)
            self._line(pixels, left, bottom - edge, right, bottom - edge, outline)
            self._line(pixels, left + edge, top, left + edge, bottom, outline)
            self._line(pixels, right - edge, top, right - edge, bottom, outline)

    def _circle(self, pixels: bytearray, cx: int, cy: int, radius: int, color: tuple[int, int, int]) -> None:
        for y in range(-radius, radius + 1):
            extent = int((radius * radius - y * y) ** .5)
            self._line(pixels, cx - extent, cy + y, cx + extent, cy + y, color, 3)

    def _arrow(self, pixels: bytearray, x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int], thickness: int = 4) -> None:
        self._line(pixels, x1, y1, x2, y2, color, thickness)
        angle = atan2(y2 - y1, x2 - x1)
        length = 22
        for offset in (-.65, .65):
            x = int(x2 - length * cos(angle + offset))
            y = int(y2 - length * sin(angle + offset))
            self._line(pixels, x2, y2, x, y, color, thickness)

    def _write_ppm(self, path: Path, pixels: bytearray) -> None:
        path.write_bytes(f"P6\n{self.width} {self.height}\n255\n".encode("ascii") + pixels)
