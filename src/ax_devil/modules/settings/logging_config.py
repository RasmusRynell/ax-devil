"""Advanced, opinionated logging configuration for ax-devil.

This module centralises *both* human-friendly **console** logging and two types
of **file** logging, aligning with modern best practices:

*   **JSON-structured** file logs (rotated) - ideal for log aggregation systems.
*   **Plain-text** file logs (rotated) - easy to grep or read in an editor.
*   **Colour-coded console** output - concise and readable during development.

All handlers capture the same rich context (timestamps to the millisecond,
source location, tracebacks, plus arbitrary ``extra`` fields), leveraging only
the Python standard library.

Usage
-----
>>> import advanced_logging_config as logcfg
>>> logcfg.setup_logging(debug=True)  # DEBUG to console, INFO to files
>>> logger = logcfg.get_logger(__name__)
>>> logger.info("Everything is wired up ✨")

By default, files record **INFO+** events while console shows all levels when
debug=True. Use ``file_debug=True`` to capture DEBUG to files when needed.
JSON logging is optional via ``enable_json=True``.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import logging as _logging
import logging.handlers as _handlers
import os as _os
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import QLoggingCategory

from ax_devil.modules.settings.paths import DEFAULT_JSON_LOG_FILENAME, DEFAULT_PLAIN_LOG_FILENAME, LOGS_DIR

# Colour codes for console output (simplified ANSI).

_RESET = "\033[0m"
_COLORS: Mapping[str, str] = {
    "DEBUG": "\033[90m",  # grey
    "INFO": "\033[97m",  # white
    "WARNING": "\033[93m",  # yellow
    "ERROR": "\033[91m",  # red
    "CRITICAL": "\033[95m",  # magenta
}
QT_ACCESSIBILITY_ATSPI_CATEGORY = "qt.accessibility.atspi"
QT_ACCESSIBILITY_ATSPI_LOGGING_RULE = f"{QT_ACCESSIBILITY_ATSPI_CATEGORY}=false"


def qt_logging_rules_with_defaults(existing_rules: str | None) -> str:
    """Add ax-devil's default Qt logging rules without overriding user rules."""
    rules = (existing_rules or "").strip()
    if QT_ACCESSIBILITY_ATSPI_CATEGORY in rules:
        return rules
    if not rules:
        return QT_ACCESSIBILITY_ATSPI_LOGGING_RULE
    return f"{rules}\n{QT_ACCESSIBILITY_ATSPI_LOGGING_RULE}"


def setup_qt_logging() -> None:
    """Install default Qt logging rules before QApplication starts."""
    rules = qt_logging_rules_with_defaults(_os.environ.get("QT_LOGGING_RULES"))
    _os.environ["QT_LOGGING_RULES"] = rules
    QLoggingCategory.setFilterRules(rules)


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


class _JsonFormatter(_logging.Formatter):
    """Minimal JSON formatter - dependency-free."""

    _BASE_KEYS = (
        "timestamp",
        "level",
        "logger",
        "file",
        "line",
        "func",
        "message",
    )

    def format(self, record: _logging.LogRecord) -> str:  # noqa: D401, N802
        payload: MutableMapping[str, Any] = {
            "timestamp": _dt.datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "file": record.pathname,
            "line": record.lineno,
            "func": record.funcName,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k not in self._BASE_KEYS and k not in (
                "msg",
                "args",
                "exc_info",
                "stack_info",
            ):
                payload[k] = v
        return _json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


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
    enable_json: bool = False,
    json_log_file: Path | None = None,
    plain_log_file: Path | None = None,
    max_file_size: int = 25 * 1024 * 1024,  # 25 MB
    backup_count: int = 10,
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

    if json_log_file is None:
        json_log_file = logs_path / DEFAULT_JSON_LOG_FILENAME
    if plain_log_file is None:
        plain_log_file = logs_path / DEFAULT_PLAIN_LOG_FILENAME

    root.handlers.clear()
    root.setLevel(_logging.DEBUG)  # capture everything; handlers decide display

    # Plain-text file handler.
    plain_handler = _handlers.RotatingFileHandler(
        filename=plain_log_file,
        maxBytes=max_file_size,
        backupCount=backup_count,
        encoding="utf-8",
    )
    plain_handler.setLevel(file_numeric_level)
    plain_handler.setFormatter(_PlainFormatter())
    root.addHandler(plain_handler)

    # JSON file handler (optional).
    if enable_json:
        json_handler = _handlers.RotatingFileHandler(
            filename=json_log_file,
            maxBytes=max_file_size,
            backupCount=backup_count,
            encoding="utf-8",
        )
        json_handler.setLevel(file_numeric_level)
        json_handler.setFormatter(_JsonFormatter())
        root.addHandler(json_handler)

    # Console handler.
    console_handler = _logging.StreamHandler()
    console_handler.setLevel(console_numeric_level)
    console_handler.setFormatter(_ColorFormatter())
    root.addHandler(console_handler)

    # Quiet noisy libs
    for noisy in ("urllib3", "botocore", "s3transfer", "ax_devil_rtsp"):
        _logging.getLogger(noisy).setLevel(_logging.WARNING)

    logger = get_logger("main")
    extra_info = {
        "console_level": _logging.getLevelName(console_numeric_level),
        "file_level": _logging.getLevelName(file_numeric_level),
        "plain_file": str(plain_log_file),
        "rotation_mb": max_file_size // (1024 * 1024),
        "backups": backup_count,
    }
    if enable_json:
        extra_info["json_file"] = str(json_log_file)

    logger.debug("Logging initialized", extra=extra_info)
    return logger


def get_logger(name: str) -> _logging.Logger:  # noqa: D401
    """Return a child logger in the ax-devil namespace."""
    return _logging.getLogger(f"ax_devil.{name}")
