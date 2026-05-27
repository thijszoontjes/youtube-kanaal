from pathlib import Path

from PIL import Image

from youtube_kanaal.config import Settings
from youtube_kanaal.services.thumbnail_service import ThumbnailService


def test_thumbnail_service_generates_readable_varied_jpegs(tmp_path: Path) -> None:
    background = tmp_path / "background.jpg"
    Image.new("RGB", (1920, 1080), "#26495f").save(background)
    service = ThumbnailService(Settings())

    first = service.generate(
        title_text="WHAT LIES BENEATH?",
        topic="Sahara Desert",
        background_path=background,
        output_path=tmp_path / "first.jpg",
    )
    second = service.generate(
        title_text="MANTIS SHRIMP WEAPON",
        topic="Mantis shrimp",
        background_path=background,
        output_path=tmp_path / "second.jpg",
    )

    with Image.open(first) as first_image, Image.open(second) as second_image:
        assert first_image.size == (1920, 1080)
        assert second_image.size == (1920, 1080)
        assert first_image.tobytes() != second_image.tobytes()

    assert first.stat().st_size > 100_000
    assert second.stat().st_size > 100_000
