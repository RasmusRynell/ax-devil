"""Entering a row with a thumbnail shows a picture-only hover card, placed once at the pointer."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QImage, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.chrome.hover_card import HoverCard
from ax_devil.modules.thumbnails import OpenFrames, Thumbnails
from ax_devil.modules.workspace.ui import row_hover
from ax_devil.modules.workspace.ui.browser_rows import BrowserSection, WorkspaceBrowserRow, WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.row_hover import RowHover
from ax_devil.modules.workspace.ui.section_tree import SectionTree
from tests.helpers.section_tree_rows import item_at, row_widget, show_tree


@dataclass(frozen=True)
class OpenStream:
    """A source whose picture an open viewer already has, so it is never read."""

    fresh_s: ClassVar[float] = 1.0

    def open_frame(self, open_frames: OpenFrames) -> QImage:
        return QImage(64, 48, QImage.Format.Format_RGB32)

    def read(self, token: CancelToken) -> QImage:
        raise AssertionError("an open stream is never read")


class FakeCursor:
    """The pointer position the hover refresh reads, which the tests set instead of moving the real pointer."""

    position = QPoint()

    @staticmethod
    def pos() -> QPoint:
        return FakeCursor.position


@pytest.fixture()
def tree(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> Iterator[SectionTree]:
    monkeypatch.setattr(row_hover, "INTENT_MS", 0)
    monkeypatch.setattr(row_hover, "_REFRESH_MS", 20)
    monkeypatch.setattr(row_hover, "QCursor", FakeCursor)
    thumbnails = Thumbnails(lambda _id: None)
    hover = RowHover(thumbnails)
    tree = show_tree(
        qtbot,
        WorkspaceBrowserRow(row_id="a", label="front_door", icon_kind="video", thumbnail=OpenStream()),
        WorkspaceBrowserRow(row_id="b", label="Night shift", icon_kind="playlist"),
        WorkspaceBrowserRow(row_id="c", label="garage", icon_kind="video", thumbnail=OpenStream()),
    )
    tree.activateWindow()
    qtbot.waitUntil(tree.isActiveWindow)
    hover.watch(tree)
    yield tree
    hover.hide()
    thumbnails.cleanup()


def _enter(tree: SectionTree, index: int, offset: QPoint = QPoint(4, 4)) -> QPoint:
    """Send the pointer entering row *index* at *offset* in its widget; return that global position."""
    widget = row_widget(tree, item_at(tree, index))
    local, global_pos = QPointF(offset), QPointF(widget.mapToGlobal(offset))
    FakeCursor.position = global_pos.toPoint()
    QApplication.sendEvent(widget, QEnterEvent(local, local, global_pos))
    QApplication.processEvents()  # Lets the hover intent timer run out.
    return FakeCursor.position


def _card(tree: SectionTree) -> HoverCard | None:
    return tree.window().findChild(HoverCard)


def _move_to(tree: SectionTree, global_pos: QPoint) -> None:
    local = tree.viewport().mapFromGlobal(global_pos)
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(local),
        QPointF(global_pos),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(tree.viewport(), move)


def _visible_card(qtbot: QtBot, tree: SectionTree) -> HoverCard:
    qtbot.waitUntil(lambda: (card := _card(tree)) is not None and card.isVisible())
    card = _card(tree)
    assert card is not None
    return card


def test_the_card_shows_only_the_picture_stays_where_it_was_placed_and_hides_on_leaving_or_scrolling(
    qtbot: QtBot, tree: SectionTree
) -> None:
    entered = _enter(tree, 0)
    card = _visible_card(qtbot, tree)
    assert not card.findChildren(QLabel)
    assert card.pos() == card.placement(entered)

    _move_to(tree, row_widget(tree, item_at(tree, 0)).mapToGlobal(QPoint(30, 6)))
    assert card.pos() == card.placement(entered)

    entered = _enter(tree, 2, QPoint(20, 5))
    assert card.pos() == card.placement(entered)

    _enter(tree, 1)
    assert not card.isVisible()

    _enter(tree, 0)
    _visible_card(qtbot, tree)
    wheel = QWheelEvent(
        QPointF(4, 4), QPointF(4, 4), QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
    )  # fmt: skip
    QApplication.sendEvent(tree.viewport(), wheel)
    assert not card.isVisible()


def test_a_row_without_a_thumbnail_shows_no_card(tree: SectionTree) -> None:
    _enter(tree, 1)

    assert _card(tree) is None


def test_a_row_whose_thumbnail_goes_away_gets_no_card_again(qtbot: QtBot, tree: SectionTree) -> None:
    _enter(tree, 0)
    card = _visible_card(qtbot, tree)

    tree.set_sections(
        (
            WorkspaceBrowserSection(
                BrowserSection.PLAYLISTS, (WorkspaceBrowserRow(row_id="a", label="front_door", icon_kind="video"),)
            ),
        )
    )
    _enter(tree, 0)

    assert not card.isVisible()


def test_the_card_hides_when_the_pointer_leaves_the_row_for_the_empty_viewport(qtbot: QtBot, tree: SectionTree) -> None:
    _enter(tree, 0)
    card = _visible_card(qtbot, tree)

    FakeCursor.position = tree.viewport().mapToGlobal(QPoint(10, tree.viewport().height() - 10))  # No event is sent.

    qtbot.waitUntil(lambda: not card.isVisible())
