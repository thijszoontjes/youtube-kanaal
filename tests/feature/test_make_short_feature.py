from __future__ import annotations

from youtube_kanaal.config import load_settings
from youtube_kanaal.db import Database
from youtube_kanaal.models import ShortRunRequest
from youtube_kanaal.pipelines import ShortPipeline, validate_artifact_directory


def test_make_short_pipeline_creates_expected_artifacts(configured_env) -> None:
    settings = load_settings()
    database = Database(settings.database_path)
    database.initialize()
    pipeline = ShortPipeline(settings, database)

    result = pipeline.run(
        ShortRunRequest(
            upload=False,
            debug=False,
            preferred_topic="axolotls",
            preferred_bucket="animals",
            mock_mode=True,
        )
    )

    assert result.output_path.exists()
    assert result.downloads_copy_path is not None
    assert result.downloads_copy_path.exists()
    assert result.metadata_path.exists()

    validation = validate_artifact_directory(result.run_id, settings.output_dir / result.run_id)
    assert validation.valid, validation.errors

    history = database.list_runs(limit=1)
    assert history[0].run_id == result.run_id
