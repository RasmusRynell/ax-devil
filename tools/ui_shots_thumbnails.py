"""Hover card screenshots: the picture the sidebar shows at the pointer when it enters a row, in the app.

The rows are the real ones of the sample workspace and a stubbed network scan, shown by the real section trees and
``RowHover``; the cards are the real ``HoverCard``. The pointer enters the row's widget at its middle, as Qt reports
it, and the shot is the window with the card painted in at its screen position, just down and right of that position.

Four shots show no card, because that is what the user sees: overlay lane rows, which have no picture, and rows whose
picture is not there. They are the plain window at the moment the pointer enters the row.

Two states are stand-ins, because the private display has neither a stream nor a camera:

- ``hover-card-live-synthetic`` shows an open stream whose newest frame is a SYNTHETIC gradient, supplied in place of
  a viewer's frame.
- ``hover-card-live-loading`` shows a stream that is not open while the snapshot read is held back, so there is no
  card yet. Nothing connects to a camera.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QColor, QEnterEvent, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.app import Application
from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.chrome.hover_card import HoverCard
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.thumbnails import CameraThumbnail
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.open_viewers import OpenViewers
from ax_devil.modules.workspace.ui.section_tree import ROW_ROLE
from ax_devil.modules.workspace.ui.source_row import SourceRow
from tools.ui_samples import CLIP_FPS, CLIP_FRAMES, sample_workspace
from tools.ui_shots_config import OUT, TEXT_SIZES, WINDOW_SIZES, configured
from tools.ui_shots_navigation import fill_sidebar_views, show_sidebar

SYNTHETIC_SIZE = (640, 360)


def synthetic_frame() -> QImage:
    """Return a gradient picture standing in for the newest frame of an open stream."""
    frame = QImage(*SYNTHETIC_SIZE, QImage.Format.Format_RGB32)
    gradient = QLinearGradient(0, 0, frame.width(), frame.height())
    gradient.setColorAt(0, QColor("#1b6ca8"))
    gradient.setColorAt(1, QColor("#e0a030"))
    painter = QPainter(frame)
    painter.fillRect(frame.rect(), gradient)
    painter.end()
    return frame


def rows(window: QWidget, view: str) -> Iterator[tuple[QTreeWidget, QTreeWidgetItem, WorkspaceBrowserRow]]:
    """Show sidebar *view*, expand its trees, and yield every row with its tree and item."""
    show_sidebar(window, view)
    QApplication.processEvents()
    for tree in window.findChildren(QTreeWidget):
        if not tree.isVisible():
            continue
        tree.expandAll()
        iterator = QTreeWidgetItemIterator(tree)
        while (item := iterator.value()) is not None:
            if (row := item.data(0, ROW_ROLE)) is not None:
                yield tree, item, row
            iterator += 1


def card_shot(window: QWidget, card: HoverCard | None, path: Path) -> None:
    """Save *window* with *card* painted in where it is on screen, or the plain window when there is no card."""
    shot = window.grab()
    if card is not None:
        painter = QPainter(shot)
        painter.drawPixmap(window.mapFromGlobal(card.pos()), card.grab())
        painter.end()
    shot.save(str(path))


def thumbnail_card_screenshots(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    video_file_factory: Callable[[float, int], Path],
    theme: str,
) -> None:
    """Save the hover card on a video, a playlist entry and an open stream, and the window with no card for an
    overlay lane, a stream still loading and a refused login, at both text sizes.

    Add a card with one more row in ``cards``.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob(f"{theme}-*-hover-card-*.png"):
        stale.unlink()
    config = configured(theme)
    clip = video_file_factory(CLIP_FRAMES / CLIP_FPS, CLIP_FPS)
    workspace_path = sample_workspace(tmp_path, clip)

    open_frame: list[QImage] = []
    """The synthetic newest frame of every stream while a shot of an open stream is taken."""
    original_latest_frame = OpenViewers.latest_frame
    monkeypatch.setattr(
        OpenViewers,
        "latest_frame",
        lambda self, content_id: open_frame[0] if open_frame else original_latest_frame(self, content_id),
    )
    read_started: list[str] = []

    def hold_back(self: CameraThumbnail, token: CancelToken) -> QImage:
        read_started.append(self.content_id)
        token.wait(60)
        raise AssertionError("The held-back snapshot is only ever cancelled.")

    monkeypatch.setattr(CameraThumbnail, "read", hold_back)

    window = Application(config_path=config.config_path, workspace_file=workspace_path).start(qapp)
    qtbot.addWidget(window)
    qtbot.wait(100)  # Let the window restore its remembered size before choosing ours.
    window.resize(*WINDOW_SIZES["narrow"])
    fill_sidebar_views(qtbot, monkeypatch, window)  # The network rows, among them one whose login was refused.

    def row_by(view: str, label: str, icon_kind: str, nth: int = 0) -> tuple[QTreeWidget, QTreeWidgetItem]:
        found = [
            (tree, item) for tree, item, row in rows(window, view) if (row.label, row.icon_kind) == (label, icon_kind)
        ]
        return found[nth]

    # (view, label, icon kind, which of the rows so named, name of the shot, whether a card must show)
    cards = (
        ("Offline", "Tracked video", "video", 0, "file", True),
        ("Offline", "tracks", "overlay", 0, "overlay-lane", False),
        ("Offline", "run_02", "video", 0, "playlist-entry", True),
        ("Live", "Video only", "live_video", 0, "live-synthetic", True),
        ("Live", "Video only", "live_video", 2, "live-loading", False),
        ("Live", "192.0.2.51", "live_video", 0, "refused-login", False),
    )
    for text_size in TEXT_SIZES:
        GlobalSettings().text_size = text_size
        qtbot.wait(300)
        for view, label, icon_kind, nth, name, shows in cards:
            tree, item = row_by(view, label, icon_kind, nth)
            tree.scrollToItem(item)
            QApplication.processEvents()
            if name == "live-synthetic":
                open_frame.append(synthetic_frame())
            widget = tree.itemWidget(item, 0)
            assert isinstance(widget, SourceRow)
            local = widget.rect().center()
            entered = QPointF(local)
            QApplication.sendEvent(widget, QEnterEvent(entered, entered, QPointF(widget.mapToGlobal(local))))
            if shows:
                qtbot.waitUntil(lambda: (c := window.findChild(HoverCard)) is not None and c.isVisible(), timeout=10000)
            else:
                qtbot.wait(300)
            card = window.findChild(HoverCard)
            assert (card is not None and card.isVisible()) == shows
            if name == "live-loading":
                qtbot.waitUntil(lambda: bool(read_started), timeout=5000)
            card_shot(window, card if shows else None, OUT / f"{theme}-{text_size.value}-hover-card-{name}.png")
            open_frame.clear()
            QApplication.sendEvent(tree.viewport(), QEvent(QEvent.Type.Leave))  # The pointer moves away.
            assert card is None or not card.isVisible()
