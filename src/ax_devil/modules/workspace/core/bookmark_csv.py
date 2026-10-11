"""Writing bookmarks to a CSV file, one row per bookmark, for spreadsheets and scripts."""

from __future__ import annotations

import csv
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple


class BookmarkCsvRow(NamedTuple):
    """One bookmark as the CSV file shows it: names as the user sees them, the time in seconds."""

    target: str
    entry: str
    frame: int
    time_s: float
    kind: str
    note: str


BOOKMARK_CSV_HEADER = ("target", "entry", "frame", "time", "kind", "note")


def write_bookmarks_csv(path: Path, rows: Iterable[BookmarkCsvRow]) -> None:
    """Write a header and *rows* to *path* as UTF-8 CSV, the time with millisecond precision.

    The previous file at *path* is replaced only after the new one is written; raises ``OSError`` when it cannot be.
    """
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as file:
            temporary_path = Path(file.name)
            writer = csv.writer(file)
            writer.writerow(BOOKMARK_CSV_HEADER)
            writer.writerows(
                (row.target, row.entry, row.frame, f"{row.time_s:.3f}", row.kind, row.note) for row in rows
            )
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
