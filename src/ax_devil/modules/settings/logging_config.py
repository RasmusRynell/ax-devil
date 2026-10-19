"""Colour-coded console logging and rotating plain-text application logs."""

from __future__ import annotations

import datetime as _dt
import logging as _logging
import logging.handlers as _handlers
from collections.abc import Mapping
from pathlib import Path

from ax_devil.modules.settings.paths import DEFAULT_PLAIN_LOG_FILENAME, LOGS_DIR

# Colour codes for console output (simplified ANSI).

_RESET = "\033[0m"
_COLORS: Mapping[str, str] = {
    "DEBUG": "\033[90m",  # grey
    "INFO": "\033[97m",  # white
    "WARNING": "\033[93m",  # yellow
    "ERROR": "\033[91m",  # red
    "CRITICAL": "\033[95m",  # magenta
}


def _resolve_level(level: str) -> int:
    """Translate a level name to its numeric value, defaulting to INFO."""
    return getattr(_logging, str(level).upper(), _logging.INFO)


class _ColorFormatter(_logging.Formatter):
    """Concise colourised formatter for interactive use."""

    def format(self, record: _logging.LogRecord) -> str:  # noqa: D401, N802
        color = _COLORS.get(record.levelname, "")
        time_str = _dt.datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        module_line = f"{record.module}:{record.lineno}"
        message = record.getMessage()
        return f"{color}{time_str} | {record.levelname:<8} | {module_line:<20} | {message}{_RESET}"


class _PlainFormatter(_logging.Formatter):
    """Human-readable file formatter with millisecond precision."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(module)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",  # base - milliseconds injected by formatTime
        )

    # override to append .mmm (milliseconds)
    def formatTime(self, record: _logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        dt = _dt.datetime.fromtimestamp(record.created)
        base = dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")
        return f"{base}.{dt.microsecond // 1000:03d}"


def setup_logging(
    *,
    console_log_level: str = "INFO",
    file_log_level: str = "INFO",
    console_only: bool = False,
    logs_dir: Path | str = LOGS_DIR,  # host path unless overridden by container mount
) -> _logging.Logger:
    """Initialise root + ax-devil loggers.

    When ``console_only=True`` only a colour-coded console handler is created,
    which is useful for lightweight CLI commands that should not produce log
    files.  A later call with ``console_only=False`` (the default) replaces
    those handlers with the full file + console stack.
    """

    console_numeric_level = _resolve_level(console_log_level)

    root = _logging.getLogger()

    # In console-only mode, skip setup if handlers already exist (idempotent
    # guard so repeated CLI calls don't stack handlers).
    if console_only:
        if root.handlers:
            return get_logger("main")
        root.setLevel(_logging.DEBUG)
        handler = _logging.StreamHandler()
        handler.setLevel(console_numeric_level)
        handler.setFormatter(_ColorFormatter())
        root.addHandler(handler)
        return get_logger("main")

    # Full setup — clears any previous handlers (including console-only ones).
    file_numeric_level = _resolve_level(file_log_level)

    logs_path = Path(logs_dir)
    logs_path.mkdir(parents=True, exist_ok=True)

    root.handlers.clear()
    root.setLevel(_logging.DEBUG)  # capture everything; handlers decide display

    # Plain-text file handler.
    plain_handler = _handlers.RotatingFileHandler(
        filename=logs_path / DEFAULT_PLAIN_LOG_FILENAME,
        maxBytes=25 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    plain_handler.setLevel(file_numeric_level)
    plain_handler.setFormatter(_PlainFormatter())
    root.addHandler(plain_handler)

    # Console handler.
    console_handler = _logging.StreamHandler()
    console_handler.setLevel(console_numeric_level)
    console_handler.setFormatter(_ColorFormatter())
    root.addHandler(console_handler)

    # Quiet noisy libs
    for noisy in ("urllib3", "botocore", "s3transfer", "ax_devil_rtsp"):
        _logging.getLogger(noisy).setLevel(_logging.WARNING)

    logger = get_logger("main")
    logger.debug("Logging initialized")
    return logger


def get_logger(name: str) -> _logging.Logger:  # noqa: D401
    """Return a child logger in the ax-devil namespace."""
    return _logging.getLogger(f"ax_devil.{name}")
