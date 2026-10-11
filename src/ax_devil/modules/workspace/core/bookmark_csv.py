"""Writing bookmarks to a CSV file, one row per bookmark, for spreadsheets and scripts."""

from __future__ import annotations

import csv
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

    Raises ``OSError`` when the file cannot be written.
    """
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(BOOKMARK_CSV_HEADER)
        writer.writerows((row.target, row.entry, row.frame, f"{row.time_s:.3f}", row.kind, row.note) for row in rows)
