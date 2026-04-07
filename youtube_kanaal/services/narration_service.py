from __future__ import annotations

from pathlib import Path

from youtube_kanaal.config import Settings
from youtube_kanaal.services.piper_service import PiperService
from youtube_kanaal.services.xtts_service import XTTSService


class NarrationService:
    """Dispatch narration synthesis to the configured engine."""

    def __init__(
        self,
        settings: Settings,
        *,
        piper_service: PiperService | None = None,
        xtts_service: XTTSService | None = None,
    ) -> None:
        self.settings = settings
        self.piper = piper_service or PiperService(settings)
        self.xtts = xtts_service or XTTSService(settings)

    def synthesize(self, *, text: str, output_path: Path) -> Path:
        if self.settings.narration_engine == "xtts":
            return self.xtts.synthesize(text=text, output_path=output_path)
        return self.piper.synthesize(text=text, output_path=output_path)
