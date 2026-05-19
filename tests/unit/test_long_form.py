from __future__ import annotations

from youtube_kanaal.config import Settings
from youtube_kanaal.cli import _preflight_long_pipeline_requirements
from youtube_kanaal.models import DoctorReport, GeneratedLongVideo, LongRunRequest, LongVideoSection


def _section(index: int) -> LongVideoSection:
    text = " ".join(
        [
            f"Axolotls detail {index} connects the story to clear visual context.",
            "The explanation stays conversational while giving the viewer enough time to follow the idea.",
            "That pacing supports long-form B-roll, simple transitions, and a natural chapter structure.",
        ]
        * 6
    )
    return LongVideoSection(
        title=f"Chapter {index} Detail",
        narration=text,
        visual_queries=["axolotl underwater", "axolotl close up"],
    )


def test_generated_long_video_accepts_required_duration_shape() -> None:
    content = GeneratedLongVideo(
        bucket="animals",
        topic="axolotls",
        title="Axolotls: The Strange Details Most People Miss",
        thumbnail_text="WEIRD SURVIVOR",
        description=(
            "A long visual explainer about axolotls with chapters, stock footage, narration, and upload metadata. "
            "The package is built for an English channel format with clear pacing and mobile-readable thumbnail text."
        ),
        tags=["axolotls", "animals", "science", "wildlife", "facts", "education", "biology", "explainer"],
        sections=[_section(index) for index in range(1, 8)],
        facts=[
            "Axolotls can be explained through several distinct visual details.",
            "Axolotls support a longer chapter-based story.",
            "Axolotls work well with underwater B-roll.",
            "Axolotls have enough context for a long explainer.",
            "Axolotls can be packaged with searchable metadata.",
            "Axolotls fit the existing channel theme.",
        ],
    )

    assert 510 <= content.estimated_duration_seconds() <= 660
    assert len(content.sections) == 7


def test_long_run_request_dry_run_disables_upload() -> None:
    request = LongRunRequest(upload=True, dry_run=True)

    assert request.upload is False


def test_long_preflight_does_not_require_instagram_config(monkeypatch) -> None:
    class FakeDoctorService:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def run(self) -> DoctorReport:
            return DoctorReport(checks=[])

    def fail_instagram_service(settings: Settings):
        raise AssertionError("Long-form preflight should not check Instagram config.")

    monkeypatch.setattr("youtube_kanaal.cli.DoctorService", FakeDoctorService)
    monkeypatch.setattr("youtube_kanaal.cli.InstagramService", fail_instagram_service)
    monkeypatch.setattr("youtube_kanaal.cli._narration_required_check_names", lambda settings: set())
    monkeypatch.setattr("youtube_kanaal.cli._print_narration_fallback_note", lambda settings: None)

    _preflight_long_pipeline_requirements(LongRunRequest(upload=True), Settings())
