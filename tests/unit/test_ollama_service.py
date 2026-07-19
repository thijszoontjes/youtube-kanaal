from __future__ import annotations

from youtube_kanaal.config import Settings
from youtube_kanaal.services.ollama_service import OllamaService


def test_visual_query_object_is_flattened_for_pexels() -> None:
    service = OllamaService(Settings(mock_mode=True))

    query = service._coerce_visual_query(
        {
            "subject": "International Space Station",
            "action": "orbiting Earth",
            "scale": "wide shot",
        }
    )

    assert query == "International Space Station orbiting Earth wide shot"
