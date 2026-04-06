from __future__ import annotations

from youtube_kanaal.config import load_settings
from youtube_kanaal.services.pexels_service import PexelsService


def test_pexels_service_expands_boolean_queries(configured_env) -> None:
    service = PexelsService(load_settings())

    expanded = service._expand_queries('"coral reef" or "reef ecosystem"')

    assert expanded == ["coral reef", "reef ecosystem"]
