from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, timezone
import os
import subprocess
import shutil
import sys
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import typer
from rich.console import Console
from rich.table import Table

from youtube_kanaal.config import Settings, load_settings, project_root
from youtube_kanaal.db import Database
from youtube_kanaal.exceptions import PipelineStageError, YoutubeKanaalError
from youtube_kanaal.models import BatchRequest, ShortRunRequest
from youtube_kanaal.models.content import TOPIC_CATALOG
from youtube_kanaal.pipelines import ShortPipeline, validate_artifact_directory
from youtube_kanaal.services.doctor import DoctorService
from youtube_kanaal.services.ffmpeg_service import FFmpegService
from youtube_kanaal.services.narration_service import NarrationService
from youtube_kanaal.services.online_runtime_service import OnlineRuntimeService
from youtube_kanaal.services.pexels_service import PexelsService
from youtube_kanaal.services.xtts_service import XTTSService
from youtube_kanaal.services.youtube_service import YouTubeService
from youtube_kanaal.utils.process import run_command
from youtube_kanaal.utils.scheduling import (
    build_windows_task_action,
    build_windows_task_name,
    build_linux_cron_block,
    parse_schedule_times,
)

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
    if result.privacy_status:
        table.add_row("Privacy", result.privacy_status)
    if result.scheduled_publish_at:
        table.add_row("Scheduled publish", result.scheduled_publish_at.isoformat())
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


def _run_pipeline_result(request: ShortRunRequest):
    _, pipeline = _bootstrap(debug=request.debug, mock_mode=request.mock_mode)
    _preflight_pipeline_requirements(request, pipeline.settings)
    try:
        return pipeline.run(request)
    except Exception as exc:
        _print_failure(exc)
        raise typer.Exit(code=1) from exc


def _run_pipeline(request: ShortRunRequest) -> None:
    result = _run_pipeline_result(request)
    _render_result(result)


def _resolve_schedule_date(
    *,
    date_text: str | None,
    timezone_name: str,
) -> date:
    schedule_timezone = _load_schedule_timezone(timezone_name)
    if date_text:
        try:
            return date.fromisoformat(date_text)
        except ValueError as exc:
            raise typer.BadParameter("Date must use YYYY-MM-DD format.") from exc
    return datetime.now(schedule_timezone).date() + timedelta(days=1)


def _build_publish_schedule(
    *,
    times: list[str],
    target_date: date,
    timezone_name: str,
) -> list[datetime]:
    schedule_timezone = _load_schedule_timezone(timezone_name)
    publish_slots: list[datetime] = []
    now_utc = datetime.now(timezone.utc)
    for time_value in times:
        hours, minutes = [int(part) for part in time_value.split(":", maxsplit=1)]
        publish_at = datetime.combine(target_date, dt_time(hour=hours, minute=minutes), tzinfo=schedule_timezone)
        if publish_at.astimezone(timezone.utc) <= now_utc:
            raise typer.BadParameter(
                f"Scheduled slot {publish_at.isoformat()} is not in the future."
            )
        publish_slots.append(publish_at)
    return publish_slots


def _load_schedule_timezone(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name.strip().upper() in {"UTC", "ETC/UTC"}:
            return timezone.utc
        local_timezone = datetime.now().astimezone().tzinfo
        if local_timezone is not None:
            return local_timezone
        raise typer.BadParameter(
            f"Timezone '{timezone_name}' is not available on this machine."
        )


def _resolve_download_copy_behavior(*, upload: bool, no_downloads: bool) -> bool:
    if upload:
        return False
    return not no_downloads


def _preflight_pipeline_requirements(request: ShortRunRequest, settings: Settings) -> None:
    if request.mock_mode:
        return

    report = DoctorService(settings).run()
    required_names = _pipeline_required_check_names(settings, upload=request.upload)
    blocking = [
        check
        for check in report.checks
        if check.name in required_names and check.status in {"fail", "warn"}
    ]
    if not blocking:
        _print_narration_fallback_note(settings)
        return

    console.print("[red]Pipeline prerequisites are not ready.[/red]")
    for check in blocking:
        console.print(f"- {check.name}: {check.status} | {check.action or check.details}")
    console.print("Run `python -m youtube_kanaal doctor` after fixing the items above.")
    raise typer.Exit(code=1)


def _pipeline_required_check_names(settings: Settings, *, upload: bool) -> set[str]:
    required_names = {
        "Python version",
        "FFmpeg",
        "Ollama reachable",
        "Ollama model",
        "Narration engine",
        "whisper.cpp",
        "whisper model path",
        "Pexels API key",
        "Downloads folder",
    }
    required_names.update(_narration_required_check_names(settings))
    if upload:
        required_names.add("YouTube OAuth client JSON")
    return required_names


def _narration_required_check_names(settings: Settings) -> set[str]:
    inspection = NarrationService(settings).inspect()
    if inspection.resolved_engine == "kokoro":
        return {"Kokoro"}
    if inspection.resolved_engine == "xtts":
        return {"XTTS runtime", "XTTS speaker samples"}
    return {"Piper", "Piper voice model"}


def _print_narration_fallback_note(settings: Settings) -> None:
    inspection = NarrationService(settings).inspect()
    if inspection.requested_engine == "kokoro" and inspection.resolved_engine == "piper" and inspection.fallback_reason:
        console.print(f"[yellow]Using Piper fallback:[/yellow] {inspection.fallback_reason}")
    if inspection.requested_engine == "xtts" and inspection.resolved_engine == "piper" and inspection.fallback_reason:
        console.print(f"[yellow]Using Piper fallback:[/yellow] {inspection.fallback_reason}")


def _preflight_narration_requirements(settings: Settings) -> None:
    if settings.mock_mode:
        return

    report = DoctorService(settings).run()
    required_names = {"FFmpeg", "Narration engine", *_narration_required_check_names(settings)}
    blocking = [
        check
        for check in report.checks
        if check.name in required_names and check.status in {"fail", "warn"}
    ]
    if not blocking:
        _print_narration_fallback_note(settings)
        return

    console.print("[red]Narration prerequisites are not ready.[/red]")
    for check in blocking:
        console.print(f"- {check.name}: {check.status} | {check.action or check.details}")
    console.print("Run `python -m youtube_kanaal doctor` after fixing the items above.")
    raise typer.Exit(code=1)


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


def _resolve_scheduled_python(python_executable: Path | None) -> Path:
    if python_executable:
        resolved = python_executable.expanduser().resolve()
        if not resolved.exists():
            raise typer.BadParameter(f"Python executable not found: {resolved}")
        return resolved

    repo_root = project_root()
    candidates = [
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return Path(sys.executable).resolve()


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
            save_to_downloads=_resolve_download_copy_behavior(upload=upload, no_downloads=no_downloads),
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
                    save_to_downloads=not batch.upload,
                    mock_mode=batch.mock_mode,
                )
            )
        except typer.Exit:
            failures += 1
    if failures:
        raise typer.Exit(code=1)


@app.command()
def make_short_schedule(
    times: str = typer.Option(
        "10:00,13:00,15:00,19:00",
        help="Comma-separated local publish times in HH:MM format.",
    ),
    date_text: Optional[str] = typer.Option(
        None,
        "--date",
        help="Target local date in YYYY-MM-DD. Defaults to tomorrow in SCHEDULED_TIMEZONE.",
    ),
    debug: bool = typer.Option(False, help="Enable verbose logging."),
    no_downloads: bool = typer.Option(False, help="Skip copying the final MP4s to Downloads."),
    mock_mode: bool = typer.Option(False, help="Use deterministic mock services."),
) -> None:
    """Generate multiple Shorts and upload them as scheduled YouTube videos."""

    settings = load_settings(app_debug=debug, mock_mode=mock_mode)
    schedule_times = parse_schedule_times(times)
    target_date = _resolve_schedule_date(
        date_text=date_text,
        timezone_name=settings.scheduled_timezone,
    )
    publish_slots = _build_publish_schedule(
        times=schedule_times,
        target_date=target_date,
        timezone_name=settings.scheduled_timezone,
    )

    console.print(
        f"Planning {len(publish_slots)} Shorts for {target_date.isoformat()} in {settings.scheduled_timezone}."
    )
    failures = 0
    scheduled_results: list[tuple[str, datetime, str | None]] = []
    for index, publish_at in enumerate(publish_slots, start=1):
        console.print(
            f"[cyan]Running scheduled Short {index}/{len(publish_slots)} for {publish_at.isoformat()}[/cyan]"
        )
        try:
            result = _run_pipeline_result(
                ShortRunRequest(
                    upload=True,
                    debug=debug,
                    privacy_status="private",
                    scheduled_publish_at=publish_at,
                    save_to_downloads=False,
                    mock_mode=mock_mode,
                )
            )
        except typer.Exit:
            failures += 1
            continue
        _render_result(result)
        scheduled_results.append((result.run_id, publish_at, result.youtube_video_id))

    if scheduled_results:
        table = Table(title="Scheduled Uploads")
        table.add_column("Run ID")
        table.add_column("Local publish time")
        table.add_column("YouTube video ID")
        for run_id, publish_at, youtube_video_id in scheduled_results:
            table.add_row(run_id, publish_at.isoformat(), youtube_video_id or "")
        console.print(table)
    if failures:
        raise typer.Exit(code=1)


@app.command()
def list_topics(
    bucket: Optional[str] = typer.Option(None, help="Filter the output to one bucket."),
) -> None:
    """List the curated topic catalog used by topic selection and forced topics."""

    rows = list(TOPIC_CATALOG.items())
    if bucket:
        normalized_bucket = bucket.strip().lower()
        rows = [(name, topics) for name, topics in rows if name == normalized_bucket]
        if not rows:
            available = ", ".join(TOPIC_CATALOG)
            raise typer.BadParameter(f"Unknown bucket '{bucket}'. Available buckets: {available}")

    table = Table(title="Topic Catalog")
    table.add_column("Bucket")
    table.add_column("Count", justify="right")
    table.add_column("Topics")
    for bucket_name, topics in rows:
        table.add_row(bucket_name, str(len(topics)), ", ".join(topics))
    console.print(table)


@app.command()
def scheduled_run(
    upload: bool = typer.Option(True, help="Upload the generated Short after rendering."),
    debug: bool = typer.Option(False, help="Enable verbose logging."),
    privacy_status: Optional[str] = typer.Option(None, help="YouTube privacy status."),
    no_downloads: bool = typer.Option(False, help="Skip copying the final MP4 to Downloads."),
) -> None:
    """Run one scheduled automation cycle."""

    _run_pipeline(
        ShortRunRequest(
            upload=upload,
            debug=debug,
            privacy_status=privacy_status,
            save_to_downloads=_resolve_download_copy_behavior(upload=upload, no_downloads=no_downloads),
            mock_mode=False,
        )
    )


@app.command()
def install_windows_schedule(
    times: Optional[str] = typer.Option(
        None,
        help="Comma-separated local times in HH:MM format. Defaults to SCHEDULED_RUN_TIMES.",
    ),
    upload: bool = typer.Option(True, help="Upload automatically after each scheduled render."),
    debug: bool = typer.Option(False, help="Enable verbose logging for scheduled runs."),
    privacy_status: Optional[str] = typer.Option(None, help="Optional privacy override for scheduled uploads."),
    task_prefix: Optional[str] = typer.Option(None, help="Task Scheduler name prefix."),
    python_executable: Optional[Path] = typer.Option(None, help="Python executable to use for the scheduled tasks."),
    force: bool = typer.Option(True, help="Overwrite existing scheduled tasks with the same names."),
) -> None:
    """Install daily Windows Task Scheduler jobs for automated Shorts."""

    if sys.platform != "win32":
        raise typer.BadParameter("install-windows-schedule is only supported on Windows.")

    settings = load_settings()
    schedule_times = parse_schedule_times(times or settings.scheduled_run_times)
    prefix = task_prefix or settings.scheduled_task_prefix
    repo_root = project_root().resolve()
    script_path = (repo_root / "scripts" / "run_scheduled_short.ps1").resolve()
    if not script_path.exists():
        raise typer.BadParameter(f"Missing scheduler script: {script_path}")
    python_path = _resolve_scheduled_python(python_executable)

    installed_tasks: list[str] = []
    for time_value in schedule_times:
        task_name = build_windows_task_name(prefix=prefix, time_value=time_value)
        action = build_windows_task_action(
            script_path=script_path,
            repo_root=repo_root,
            python_executable=python_path,
            upload=upload,
            debug=debug,
            privacy_status=privacy_status,
        )
        command = [
            "schtasks",
            "/Create",
            "/SC",
            "DAILY",
            "/ST",
            time_value,
            "/TN",
            task_name,
            "/TR",
            action,
        ]
        if force:
            command.append("/F")
        run_command(command, timeout_seconds=120, stage="schedule_install")
        installed_tasks.append(task_name)

    table = Table(title="Windows Schedule Installed")
    table.add_column("Task")
    table.add_column("Time")
    table.add_column("Upload")
    table.add_column("Action")
    for task_name, time_value in zip(installed_tasks, schedule_times):
        table.add_row(task_name, time_value, "yes" if upload else "no", "scheduled-run")
    console.print(table)
    console.print(f"Scheduled tasks use local Windows time on this machine and call {script_path}.")


@app.command()
def install_linux_schedule(
    times: Optional[str] = typer.Option(
        None,
        help="Comma-separated local times in HH:MM format. Defaults to SCHEDULED_RUN_TIMES.",
    ),
    timezone: Optional[str] = typer.Option(
        None,
        help="IANA timezone such as Europe/Amsterdam. Defaults to SCHEDULED_TIMEZONE.",
    ),
    upload: bool = typer.Option(True, help="Upload automatically after each scheduled render."),
    debug: bool = typer.Option(False, help="Enable verbose logging for scheduled runs."),
    privacy_status: Optional[str] = typer.Option(None, help="Optional privacy override for scheduled uploads."),
    python_executable: Optional[Path] = typer.Option(None, help="Python executable to use for the scheduled jobs."),
) -> None:
    """Install Linux cron jobs for automated Shorts."""

    if os.name == "nt":
        raise typer.BadParameter("install-linux-schedule is only supported on Linux/macOS shells.")

    settings = load_settings()
    schedule_times = parse_schedule_times(times or settings.scheduled_run_times)
    schedule_timezone = timezone or settings.scheduled_timezone
    repo_root = project_root().resolve()
    script_path = (repo_root / "scripts" / "run_scheduled_short.sh").resolve()
    if not script_path.exists():
        raise typer.BadParameter(f"Missing scheduler script: {script_path}")
    python_path = _resolve_scheduled_python(python_executable)
    log_path = (settings.logs_dir / "scheduled-cron.log").resolve()
    marker = settings.scheduled_task_prefix
    block = build_linux_cron_block(
        marker=marker,
        script_path=script_path,
        repo_root=repo_root,
        python_executable=python_path,
        times=schedule_times,
        timezone=schedule_timezone,
        upload=upload,
        debug=debug,
        privacy_status=privacy_status,
        log_path=log_path,
    )

    current_crontab = _read_current_crontab()
    updated_crontab = _replace_managed_cron_block(current_crontab, marker=marker, replacement=block)
    run_command(
        ["crontab", "-"],
        input_text=updated_crontab,
        timeout_seconds=120,
        stage="schedule_install",
    )

    table = Table(title="Linux Schedule Installed")
    table.add_column("Time")
    table.add_column("Timezone")
    table.add_column("Upload")
    table.add_column("Command")
    for time_value in schedule_times:
        table.add_row(time_value, schedule_timezone, "yes" if upload else "no", "scheduled-run")
    console.print(table)
    console.print(f"Cron logs are appended to {log_path}.")


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
def prepare_online_runtime(debug: bool = typer.Option(False, help="Enable verbose logging.")) -> None:
    """Write online secrets and voice sample files from environment variables."""

    settings = load_settings(app_debug=debug)
    service = OnlineRuntimeService(settings)
    results = service.materialize_from_environment()
    if not results:
        console.print("No ONLINE_* environment variables were found. Nothing was written.")
        return

    table = Table(title="Online Runtime Prepared")
    table.add_column("Kind")
    table.add_column("Path")
    table.add_column("Source")
    for item in results:
        table.add_row(item["kind"], item["path"], item["source_env"])
    console.print(table)


@app.command()
def diagnose_voice(
    debug: bool = typer.Option(False, help="Enable verbose logging."),
    mock_mode: bool = typer.Option(False, help="Use deterministic mock audio."),
) -> None:
    """Inspect the terminal voice setup without rendering a full Short."""

    settings = load_settings(app_debug=debug, mock_mode=mock_mode)
    narration = NarrationService(settings)
    inspection = narration.inspect()
    xtts = XTTSService(settings)

    table = Table(title="Voice Diagnostics")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Requested engine", inspection.requested_engine)
    table.add_row("Resolved engine", inspection.resolved_engine)
    table.add_row("Fallback reason", inspection.fallback_reason or "none")
    table.add_row("Kokoro ready", "yes" if inspection.kokoro_ready else "no")
    table.add_row("Kokoro details", inspection.kokoro_reason or "ready")
    table.add_row("XTTS runtime ready", "yes" if inspection.xtts_runtime_ready else "no")
    table.add_row("XTTS runtime details", inspection.xtts_runtime_reason or "ready")
    table.add_row("Piper ready", "yes" if inspection.piper_ready else "no")
    table.add_row("Piper details", inspection.piper_reason or "ready")
    table.add_row("Reference clips", str(len(inspection.reference_sources)))
    console.print(table)

    source_details = xtts.describe_reference_sources()
    if source_details:
        source_table = Table(title="Reference Audio")
        source_table.add_column("Name")
        source_table.add_column("Path")
        source_table.add_column("Ext")
        source_table.add_column("Codec")
        source_table.add_column("Hz")
        source_table.add_column("Ch")
        source_table.add_column("Duration")
        for source in source_details:
            source_table.add_row(
                Path(str(source.get("source_path") or "")).name,
                str(source.get("source_path") or ""),
                str(source.get("source_extension") or ""),
                str(source.get("codec_name") or source.get("probe_status") or ""),
                str(source.get("sample_rate") or ""),
                str(source.get("channels") or ""),
                str(source.get("duration_seconds") or ""),
            )
        console.print(source_table)


@app.command()
def preview_voice(
    text: str = typer.Argument(..., help="Text to speak with the active narration engine."),
    output: Optional[Path] = typer.Option(None, help="Optional output WAV path."),
    debug: bool = typer.Option(False, help="Enable verbose logging."),
    mock_mode: bool = typer.Option(False, help="Use deterministic mock audio."),
) -> None:
    """Render a quick WAV preview of the active voice setup."""

    settings = load_settings(app_debug=debug, mock_mode=mock_mode)
    _preflight_narration_requirements(settings)
    narration = NarrationService(settings)
    active_engine = narration.resolve_engine()

    preview_dir = settings.output_dir / "voice-preview"
    raw_path = preview_dir / "preview_raw.wav"
    final_path = output.expanduser().resolve() if output else preview_dir / "preview.wav"
    final_path.parent.mkdir(parents=True, exist_ok=True)

    synthesis = narration.synthesize(text=text, output_path=raw_path)
    FFmpegService(settings).normalize_audio(input_path=raw_path, output_path=final_path)
    if synthesis.fallback_reason:
        console.print(
            f"Voice preview written to {final_path} using {synthesis.engine_used}. "
            f"Fallback reason: {synthesis.fallback_reason}"
        )
        return
    console.print(f"Voice preview written to {final_path} using {active_engine}.")


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
            save_to_downloads=not upload,
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


def _read_current_crontab() -> str:
    result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode == 0:
        return result.stdout
    stderr = (result.stderr or "").lower()
    if "no crontab" in stderr:
        return ""
    raise typer.BadParameter(f"Could not read the current crontab: {(result.stderr or result.stdout).strip()}")


def _replace_managed_cron_block(current_crontab: str, *, marker: str, replacement: str) -> str:
    start_marker = f"# >>> {marker} >>>"
    end_marker = f"# <<< {marker} <<<"
    if start_marker in current_crontab and end_marker in current_crontab:
        before, remainder = current_crontab.split(start_marker, 1)
        _, after = remainder.split(end_marker, 1)
        current_crontab = before.rstrip() + "\n" + after.lstrip("\n")
    base = current_crontab.rstrip()
    if base:
        return base + "\n\n" + replacement
    return replacement


if __name__ == "__main__":
    main()
