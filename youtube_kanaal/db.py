from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from youtube_kanaal.models.run import HistoryEntry


class Database:
    """Thin SQLite repository for run history and dedupe state."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    current_stage TEXT,
                    bucket TEXT,
                    topic TEXT,
                    title TEXT,
                    upload_requested INTEGER NOT NULL DEFAULT 0,
                    upload_status TEXT,
                    output_path TEXT,
                    downloads_path TEXT,
                    log_path TEXT,
                    metadata_json TEXT,
                    error_stage TEXT,
                    error_message TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    duration_seconds REAL,
                    mock_mode INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    normalized_topic TEXT NOT NULL UNIQUE,
                    topic TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    title TEXT,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    youtube_video_id TEXT,
                    privacy_status TEXT,
                    response_json TEXT,
                    uploaded_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    source_id TEXT,
                    source_url TEXT,
                    local_path TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs (started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_topics_created_at ON topics (created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_assets_run_id ON assets (run_id);
                """
            )

    def insert_run(
        self,
        *,
        run_id: str,
        status: str,
        started_at: str,
        log_path: str,
        upload_requested: bool,
        mock_mode: bool,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, status, started_at, log_path, upload_requested, mock_mode
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, status, started_at, log_path, int(upload_requested), int(mock_mode)),
            )

    def update_run_stage(self, run_id: str, stage: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE runs SET current_stage = ? WHERE run_id = ?",
                (stage, run_id),
            )

    def mark_run_success(
        self,
        *,
        run_id: str,
        bucket: str,
        topic: str,
        title: str,
        output_path: str,
        downloads_path: str | None,
        metadata: dict[str, Any],
        completed_at: str,
        duration_seconds: float,
        upload_status: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?, current_stage = ?, bucket = ?, topic = ?, title = ?,
                    output_path = ?, downloads_path = ?, metadata_json = ?, completed_at = ?,
                    duration_seconds = ?, upload_status = ?
                WHERE run_id = ?
                """,
                (
                    "succeeded",
                    "completed",
                    bucket,
                    topic,
                    title,
                    output_path,
                    downloads_path,
                    json.dumps(metadata, ensure_ascii=True),
                    completed_at,
                    duration_seconds,
                    upload_status,
                    run_id,
                ),
            )

    def mark_run_failed(
        self,
        *,
        run_id: str,
        stage: str,
        error_message: str,
        completed_at: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?, current_stage = ?, error_stage = ?, error_message = ?, completed_at = ?
                WHERE run_id = ?
                """,
                ("failed", stage, stage, error_message, completed_at, run_id),
            )

    def record_topic(
        self,
        *,
        topic: str,
        bucket: str,
        title: str,
        run_id: str,
        created_at: str,
        normalized_topic: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO topics (
                    normalized_topic, topic, bucket, title, run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized_topic, topic, bucket, title, run_id, created_at),
            )

    def record_asset(
        self,
        *,
        run_id: str,
        asset_type: str,
        source_id: str | None,
        source_url: str | None,
        local_path: str,
        metadata: dict[str, Any],
        created_at: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO assets (
                    run_id, asset_type, source_id, source_url, local_path, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    asset_type,
                    source_id,
                    source_url,
                    local_path,
                    json.dumps(metadata, ensure_ascii=True),
                    created_at,
                ),
            )

    def record_upload(
        self,
        *,
        run_id: str,
        youtube_video_id: str | None,
        privacy_status: str,
        response: dict[str, Any],
        uploaded_at: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO uploads (run_id, youtube_video_id, privacy_status, response_json, uploaded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    youtube_video_id,
                    privacy_status,
                    json.dumps(response, ensure_ascii=True),
                    uploaded_at,
                ),
            )

    def list_runs(self, limit: int = 20) -> list[HistoryEntry]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, status, topic, title, started_at, duration_seconds, output_path
                FROM runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [HistoryEntry.model_validate(dict(row)) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return dict(row) if row else None

    def recent_topics(self, limit: int = 100) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT topic FROM topics ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [row["topic"] for row in rows]

    def recent_titles(self, limit: int = 100) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT title FROM topics WHERE title IS NOT NULL ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [row["title"] for row in rows if row["title"]]
