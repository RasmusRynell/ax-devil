"""Bookmarks exported as CSV read back as the rows written, whatever the notes hold."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

import pytest

from ax_devil.modules.workspace.core.bookmark_csv import BookmarkCsvRow, write_bookmarks_csv


def test_the_csv_has_a_header_and_one_row_per_bookmark_with_notes_kept_intact(tmp_path: Path) -> None:
    path = tmp_path / "bookmarks.csv"
    rows = [
        BookmarkCsvRow("clip.mp4", "", 30, 1.0, "Issue", 'Car 8, "deleted"\nat the edge'),
        BookmarkCsvRow("Night shift", "run_02", 41, 1.3667, "Note", "Fußgänger"),
    ]

    write_bookmarks_csv(path, rows)

    with path.open(encoding="utf-8", newline="") as file:
        assert list(csv.reader(file)) == [
            ["target", "entry", "frame", "time", "kind", "note"],
            ["clip.mp4", "", "30", "1.000", "Issue", 'Car 8, "deleted"\nat the edge'],
            ["Night shift", "run_02", "41", "1.367", "Note", "Fußgänger"],
        ]


def test_a_failed_export_keeps_the_previous_file_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "bookmarks.csv"
    path.write_text("previous", encoding="utf-8")

    def failing_rows() -> Iterator[BookmarkCsvRow]:
        yield BookmarkCsvRow("clip.mp4", "", 30, 1.0, "Issue", "")
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        write_bookmarks_csv(path, failing_rows())

    assert path.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.iterdir()) == [path]
