from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from youtube_kanaal.config import load_settings, project_root
from youtube_kanaal.db import Database
from youtube_kanaal.exceptions import PipelineStageError, YoutubeKanaalError
from youtube_kanaal.models import BatchRequest, ShortRunRequest
from youtube_kanaal.pipelines import ShortPipeline, validate_artifact_directory
from youtube_kanaal.services.doctor import DoctorService
from youtube_kanaal.services.pexels_service import PexelsService
from youtube_kanaal.services.youtube_service import YouTubeService

app = typer.Typer(help="Local YouTube Shorts pipeline.", add_completion=False)
console = Console()


def _bootstrap(debug: bool = False, mock_mode: bool = False) -> tuple[Database, ShortPipeline]:
    settings = load_settings(app_debug=debug, mock_mode=mock_mode)
    database = Database(settings.database_path)
    database.initialize()
    pipeline = ShortPipeline(settings, database)
    return database, pipeline


def _render_result(result) -> None:
    table = Table(title="Short Completed")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Title", result.title)
    table.add_row("Topic", result.topic)
    table.add_row("Duration", f"{result.duration_seconds:.2f}s")
    table.add_row("Output", str(result.output_path))
    table.add_row("Downloads copy", str(result.downloads_copy_path or "not copied"))
    table.add_row("Uploaded", "yes" if result.uploaded else "no")
    table.add_row("Run ID", result.run_id)
    table.add_row("Log file", str(result.log_path))
    console.print(table)


def _print_failure(error: Exception) -> None:
    if isinstance(error, PipelineStageError):
        console.print(f"[red]What failed:[/red] {error.stage}")
        console.print(f"[red]Why it probably failed:[/red] {error.probable_cause or error.message}")
        if error.details_path:
            console.print(f"[yellow]Inspect:[/yellow] {error.details_path}")
        console.print("[yellow]How to retry:[/yellow] fix the dependency or config, then rerun the same command.")
    else:
        console.print(f"[red]Pipeline failed:[/red] {error}")


def _run_pipeline(request: ShortRunRequest) -> None:
    _, pipeline = _bootstrap(debug=request.debug, mock_mode=request.mock_mode)
    try:
        result = pipeline.run(request)
    except Exception as exc:
        _print_failure(exc)
        raise typer.Exit(code=1) from exc
    _render_result(result)


def _update_env_file(env_path: Path, key: str, value: str) -> None:
    existing = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    replaced = False
    output_lines: list[str] = []
    for line in existing:
        if line.startswith(f"{key}="):
            output_lines.append(f"{key}={value}")
            replaced = True
        else:
            output_lines.append(line)
    if not replaced:
        output_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(output_lines).strip() + "\n", encoding="utf-8")


@app.command()
def make_short(
    upload: bool = typer.Option(False, help="Upload to YouTube after rendering."),
    debug: bool = typer.Option(False, help="Enable verbose logging."),
    topic: Optional[str] = typer.Option(None, help="Force a specific catalog topic."),
    bucket: Optional[str] = typer.Option(None, help="Optional bucket for a forced topic."),
    privacy_status: Optional[str] = typer.Option(None, help="YouTube privacy status."),
    no_downloads: bool = typer.Option(False, help="Skip copying the final MP4 to Downloads."),
    mock_mode: bool = typer.Option(False, help="Use deterministic mock services."),
) -> None:
    """Generate one short."""

    _run_pipeline(
        ShortRunRequest(
            upload=upload,
            debug=debug,
            preferred_topic=topic,
            preferred_bucket=bucket,
            privacy_status=privacy_status,
            save_to_downloads=not no_downloads,
            mock_mode=mock_mode,
        )
    )


@app.command()
def make_batch(
    count: int = typer.Option(3, min=1, max=10, help="How many shorts to generate."),
    upload: bool = typer.Option(False, help="Upload each generated Short."),
    debug: bool = typer.Option(False, help="Enable verbose logging."),
    mock_mode: bool = typer.Option(False, help="Use deterministic mock services."),
) -> None:
    """Generate multiple shorts."""

    batch = BatchRequest(count=count, upload=upload, debug=debug, mock_mode=mock_mode)
    failures = 0
    for index in range(batch.count):
        console.print(f"[cyan]Running short {index + 1}/{batch.count}[/cyan]")
        try:
            _run_pipeline(
                ShortRunRequest(
                    upload=batch.upload,
                    debug=batch.debug,
                    mock_mode=batch.mock_mode,
                )
            )
        except typer.Exit:
            failures += 1
    if failures:
        raise typer.Exit(code=1)


@app.command()
def doctor(debug: bool = typer.Option(False, help="Enable verbose logging.")) -> None:
    """Run environment checks."""

    settings = load_settings(app_debug=debug)
    Database(settings.database_path).initialize()
    report = DoctorService(settings).run()
    table = Table(title="Environment Doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Details")
    table.add_column("Action")
    for check in report.checks:
        table.add_row(check.name, check.status, check.details, check.action or "")
    console.print(table)
    if not report.all_ok():
        raise typer.Exit(code=1)


@app.command()
def init_config(force: bool = typer.Option(False, help="Overwrite an existing .env file.")) -> None:
    """Create .env and local directories."""

    env_example = project_root() / ".env.example"
    env_file = project_root() / ".env"
    if not env_example.exists():
        raise typer.BadParameter(".env.example is missing from the project.")
    if env_file.exists() and not force:
        console.print(f"[yellow]{env_file} already exists[/yellow]")
    else:
        shutil.copy2(env_example, env_file)
        console.print(f"Created {env_file}")
    database, _ = _bootstrap()
    database.initialize()
    console.print("Initialized directories and SQLite database.")


@app.command()
def auth_youtube(
    debug: bool = typer.Option(False, help="Enable verbose logging."),
    force: bool = typer.Option(False, help="Force a fresh browser consent flow."),
) -> None:
    """Run the YouTube OAuth browser flow and save the token locally."""

    settings = load_settings(app_debug=debug)
    Database(settings.database_path).initialize()
    token_path = YouTubeService(settings).authenticate(force=force)
    console.print(f"Saved YouTube token to {token_path}")


@app.command()
def auth_pexels(
    key: Optional[str] = typer.Option(None, help="Pexels API key to validate."),
    write_env: bool = typer.Option(False, help="Write the provided key into .env."),
    debug: bool = typer.Option(False, help="Enable verbose logging."),
) -> None:
    """Validate the Pexels API key."""

    env_path = project_root() / ".env"
    if key and write_env:
        _update_env_file(env_path, "PEXELS_API_KEY", key)
    settings = load_settings(app_debug=debug, pexels_api_key=key) if key else load_settings(app_debug=debug)
    ok = PexelsService(settings).validate_credentials()
    if not ok:
        console.print("[red]Pexels authentication failed.[/red]")
        raise typer.Exit(code=1)
    console.print("Pexels API key is valid.")


@app.command()
def list_history(limit: int = typer.Option(20, min=1, max=100, help="Number of runs to show.")) -> None:
    """List recent pipeline runs."""

    settings = load_settings()
    database = Database(settings.database_path)
    database.initialize()
    rows = database.list_runs(limit=limit)
    table = Table(title="Run History")
    table.add_column("Run ID")
    table.add_column("Status")
    table.add_column("Topic")
    table.add_column("Title")
    table.add_column("Started")
    table.add_column("Duration")
    for row in rows:
        table.add_row(
            row.run_id,
            row.status,
            row.topic or "",
            row.title or "",
            row.started_at,
            f"{row.duration_seconds:.2f}s" if row.duration_seconds else "",
        )
    console.print(table)


@app.command()
def retry_run(
    run_id: str = typer.Argument(..., help="Run ID to retry."),
    upload: bool = typer.Option(False, help="Upload after retry."),
    debug: bool = typer.Option(False, help="Enable verbose logging."),
) -> None:
    """Retry a previous run using the same topic."""

    database, _ = _bootstrap(debug=debug)
    existing = database.get_run(run_id)
    if not existing:
        raise typer.BadParameter(f"Unknown run_id: {run_id}")
    _run_pipeline(
        ShortRunRequest(
            upload=upload,
            debug=debug,
            preferred_topic=existing.get("topic"),
            preferred_bucket=existing.get("bucket"),
        )
    )


@app.command()
def validate_assets(
    run_id: Optional[str] = typer.Option(None, help="Run ID to validate. Defaults to the latest run."),
) -> None:
    """Validate that a run directory contains the expected artifacts."""

    settings = load_settings()
    database = Database(settings.database_path)
    database.initialize()
    if run_id:
        target = database.get_run(run_id)
        if not target:
            raise typer.BadParameter(f"Unknown run_id: {run_id}")
        target_run_id = target["run_id"]
    else:
        rows = database.list_runs(limit=1)
        if not rows:
            raise typer.BadParameter("No runs found.")
        target_run_id = rows[0].run_id
    result = validate_artifact_directory(target_run_id, settings.output_dir / target_run_id)
    if result.valid:
        console.print(f"Artifacts valid for run {target_run_id}")
        for check in result.checks:
            console.print(f"- {check}")
        return
    console.print(f"[red]Artifacts invalid for run {target_run_id}[/red]")
    for error in result.errors:
        console.print(f"- {error}")
    raise typer.Exit(code=1)


@app.command()
def test_pipeline(debug: bool = typer.Option(False, help="Enable verbose logging.")) -> None:
    """Run a deterministic mock pipeline smoke test."""

    _run_pipeline(
        ShortRunRequest(
            upload=False,
            debug=debug,
            preferred_topic="axolotls",
            preferred_bucket="animals",
            mock_mode=True,
        )
    )


def main() -> None:
    try:
        app()
    except YoutubeKanaalError as exc:
        _print_failure(exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
