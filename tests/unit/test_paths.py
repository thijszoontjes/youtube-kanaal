from __future__ import annotations

from pathlib import Path

from youtube_kanaal.utils.files import collision_safe_path, copy_collision_safe, safe_slug


def test_safe_slug_normalizes_text() -> None:
    assert safe_slug("3 Facts About Axolotls!") == "3-facts-about-axolotls"


def test_collision_safe_copy_adds_suffix(tmp_path: Path) -> None:
    source = tmp_path / "video.mp4"
    source.write_bytes(b"data")
    destination_dir = tmp_path / "downloads"
    first = copy_collision_safe(source, destination_dir)
    second = copy_collision_safe(source, destination_dir)

    assert first.name == "video.mp4"
    assert second.name == "video-1.mp4"
    assert second.exists()


def test_collision_safe_path_returns_same_when_free(tmp_path: Path) -> None:
    candidate = tmp_path / "file.mp4"
    assert collision_safe_path(candidate) == candidate
