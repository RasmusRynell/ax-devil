"""The Add Video dialog builds a Video Item from the video and overlay files picked."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QWidget,
)
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.core import OverlayChoice, OverlayFile, VideoFileSelection, VideoItem
from ax_devil.modules.workspace.core.intake import WorkspaceDecoderOption, WorkspaceIntake
from ax_devil.modules.workspace.ui.add_content.add_video_dialog import AddVideoDialog, OverlayFileRow
from tests.helpers.add_dialogs import DialogOptionProvider, ok_button, select_data, shows_text
from tests.helpers.forms import form_field


def _recipe(item: VideoItem | None) -> tuple[str, Path, tuple[OverlayFile, ...]] | None:
    return None if item is None else (item.display_name, item.video, item.overlays)


class _VideoOptionProvider(DialogOptionProvider):
    def file_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return (
            WorkspaceDecoderOption(handler_type="TXT", display_name="Text", file_extensions=(".txt",)),
            WorkspaceDecoderOption(handler_type="FRAME", display_name="Frame", file_extensions=(".jsonl",)),
            WorkspaceDecoderOption(handler_type="TRACKS", display_name="Tracks", file_extensions=(".jsonl",)),
        )


@pytest.fixture
def video_intake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.add_video_dialog.default_workspace_intake",
        lambda: WorkspaceIntake(_VideoOptionProvider()),
    )


@pytest.fixture
def video_dialog(qtbot: QtBot, video_intake: None) -> AddVideoDialog:
    dialog = AddVideoDialog()
    qtbot.addWidget(dialog)
    return dialog


def _overlay_rows(dialog: QWidget) -> list[OverlayFileRow]:
    return dialog.findChildren(OverlayFileRow)


def _row_path(row: OverlayFileRow) -> QLineEdit:
    edit = row.findChild(QLineEdit)
    assert edit is not None
    return edit


def _row_handler(row: OverlayFileRow) -> QComboBox:
    combo = row.findChild(QComboBox)
    assert combo is not None
    return combo


def _click(dialog: QWidget, kind: type[QAbstractButton], text: str) -> None:
    """Click the button the user sees named *text*, or whose accessible name is *text*."""
    next(b for b in dialog.findChildren(kind) if text in (b.text(), b.accessibleName())).click()


def test_add_video_keeps_ok_disabled_and_says_why_until_selection_is_complete(
    video_dialog: AddVideoDialog, tmp_path: Path
) -> None:
    """Missing input disables OK with an inline reason instead of a silent no-op or an after-the-fact modal."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "overlay.jsonl"
    overlay.write_text("{}\n")
    video_edit = form_field(video_dialog, "Video File", QLineEdit)
    [row] = _overlay_rows(video_dialog)
    overlay_edit = _row_path(row)
    steps = [
        (video_edit, "", "Choose a video file to continue."),
        (video_edit, str(tmp_path / "missing.mp4"), "Video file not found."),
        (video_edit, str(video), ""),
        (overlay_edit, str(tmp_path / "missing.jsonl"), "Overlay file not found: missing.jsonl"),
        (overlay_edit, str(overlay), "Choose the data handler that reads overlay.jsonl."),
    ]
    for edit, text, message in steps:
        edit.setText(text)
        assert ok_button(video_dialog).isEnabled() is not bool(message)
        if message:
            assert shows_text(video_dialog, message)

    video_dialog.accept()
    assert video_dialog.get_result() is None

    select_data(_row_handler(row), "TRACKS")
    ok_button(video_dialog).click()

    assert _recipe(video_dialog.get_result()) == ("video.mp4", video, (OverlayFile(overlay, "TRACKS"),))


def test_add_video_selects_the_only_decoder_that_reads_the_overlay(
    video_dialog: AddVideoDialog, tmp_path: Path
) -> None:
    """An overlay extension that one decoder declares picks that handler without a manual choice."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "gt.txt"
    overlay.write_text("1,1,0,0,1,1,1,-1,-1,-1\n")
    pdf = tmp_path / "notes.pdf"
    pdf.write_bytes(b"")
    [row] = _overlay_rows(video_dialog)
    handler = _row_handler(row)
    overlay_edit = _row_path(row)

    assert not handler.isEnabled()
    form_field(video_dialog, "Video File", QLineEdit).setText(str(video))
    overlay_edit.setText(str(overlay))
    assert handler.isEnabled()
    assert handler.currentData() == "TXT"

    overlay_edit.setText(str(tmp_path / "scene.jsonl"))
    assert handler.currentData() is None
    overlay_edit.setText(str(pdf))
    assert handler.currentData() is None
    assert not ok_button(video_dialog).isEnabled()

    overlay_edit.setText(str(overlay))
    assert handler.currentData() == "TXT"
    name_edit = form_field(video_dialog, "Name", QLineEdit)
    assert name_edit.placeholderText() == "clip.mp4"
    name_edit.setText("Gate camera")
    ok_button(video_dialog).click()

    assert _recipe(video_dialog.get_result()) == ("Gate camera", video, (OverlayFile(overlay, "TXT"),))


def test_add_video_prefills_a_dropped_selection(qtbot: QtBot, video_intake: None, tmp_path: Path) -> None:
    """A dropped video and overlays open the dialog filled in, waiting only for the ambiguous handler."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    scene = tmp_path / "scene.jsonl"
    scene.write_text("{}\n")
    gt = tmp_path / "gt.txt"
    gt.write_text("1,1,0,0,1,1,1,-1,-1,-1\n")

    dialog = AddVideoDialog(initial=VideoFileSelection(video, (OverlayChoice(scene), OverlayChoice(gt, "TXT"))))
    qtbot.addWidget(dialog)

    rows = _overlay_rows(dialog)
    assert [_row_path(row).text() for row in rows] == [str(scene), str(gt)]
    assert not ok_button(dialog).isEnabled()
    assert shows_text(dialog, "Choose the data handler that reads scene.jsonl.")
    select_data(_row_handler(rows[0]), "FRAME")
    ok_button(dialog).click()
    assert _recipe(dialog.get_result()) == (
        "clip.mp4",
        video,
        (OverlayFile(scene, "FRAME"), OverlayFile(gt, "TXT")),
    )


def test_add_video_with_two_overlay_rows_makes_the_item_a_dropped_pair_makes(
    video_dialog: AddVideoDialog, tmp_path: Path
) -> None:
    """Add overlay adds a row for another file, and the item has one overlay per row, in row order."""
    video, first, second = tmp_path / "clip.mp4", tmp_path / "gt.txt", tmp_path / "scene.jsonl"
    for path in (video, first, second):
        path.write_bytes(b"")
    form_field(video_dialog, "Video File", QLineEdit).setText(str(video))

    _click(video_dialog, QPushButton, "Add overlay")
    first_row, second_row = _overlay_rows(video_dialog)
    _row_path(first_row).setText(str(first))
    _row_path(second_row).setText(str(second))
    assert shows_text(video_dialog, "Choose the data handler that reads scene.jsonl.")
    select_data(_row_handler(second_row), "TRACKS")
    ok_button(video_dialog).click()

    item = video_dialog.get_result()
    assert _recipe(item) == ("clip.mp4", video, (OverlayFile(first, "TXT"), OverlayFile(second, "TRACKS")))
    assert item is not None
    assert item.overlays == (
        VideoFileSelection(video, (OverlayChoice(first, "TXT"), OverlayChoice(second, "TRACKS"))).to_item().overlays
    )


def test_add_video_overlay_rows_can_be_removed_and_empty_rows_are_ignored(
    video_dialog: AddVideoDialog, tmp_path: Path
) -> None:
    """A row without a file adds nothing, a removed row leaves its file out, and no overlay at all is fine."""
    video, overlay = tmp_path / "clip.mp4", tmp_path / "gt.txt"
    video.write_bytes(b"")
    overlay.write_bytes(b"")
    form_field(video_dialog, "Video File", QLineEdit).setText(str(video))
    _click(video_dialog, QPushButton, "Add overlay")
    empty, filled = _overlay_rows(video_dialog)
    _row_path(filled).setText(str(overlay))
    assert ok_button(video_dialog).isEnabled()

    _click(empty, QToolButton, "Remove overlay")
    assert _overlay_rows(video_dialog) == [filled]
    _click(filled, QToolButton, "Remove overlay")
    assert _overlay_rows(video_dialog) == []
    ok_button(video_dialog).click()

    assert _recipe(video_dialog.get_result()) == ("clip.mp4", video, ())


def test_add_video_rows_added_to_a_shown_dialog_fit_without_scrolling(video_dialog: AddVideoDialog) -> None:
    """Adding overlay rows grows the open dialog instead of starting a scroll bar."""
    video_dialog.show()
    QApplication.processEvents()
    for _ in range(3):
        _click(video_dialog, QPushButton, "Add overlay")
    QApplication.processEvents()
    QApplication.processEvents()

    assert len(_overlay_rows(video_dialog)) == 4
    scroll = video_dialog.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.verticalScrollBar().maximum() == 0
