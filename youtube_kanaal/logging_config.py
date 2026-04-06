from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """Small JSON formatter for structured run logs."""

    _reserved = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in self._reserved and not key.startswith("_")
            }
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


@dataclass
class LoggingBundle:
    logger: logging.Logger
    human_log_path: Path
    structured_log_path: Path


def configure_run_logging(run_id: str, logs_dir: Path, debug: bool = False) -> LoggingBundle:
    logs_dir.mkdir(parents=True, exist_ok=True)
    human_log_path = logs_dir / f"{run_id}.log"
    structured_log_path = logs_dir / f"{run_id}.jsonl"

    logger = logging.getLogger(f"youtube_kanaal.{run_id}")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    human_handler = RotatingFileHandler(
        human_log_path,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    human_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    human_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    json_handler = RotatingFileHandler(
        structured_log_path,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    json_handler.setLevel(logging.DEBUG)
    json_handler.setFormatter(JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S"))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    logger.addHandler(human_handler)
    logger.addHandler(json_handler)
    logger.addHandler(console_handler)

    return LoggingBundle(
        logger=logger,
        human_log_path=human_log_path,
        structured_log_path=structured_log_path,
    )
