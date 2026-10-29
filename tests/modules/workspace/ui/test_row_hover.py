"""Resting the pointer on a row with a thumbnail shows a picture-only hover card that follows the pointer."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QHelpEvent, QImage, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.chrome.hover_card import HoverCard
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.thumbnails import OpenFrames, Thumbnails
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


@pytest.fixture()
def tree(qtbot: QtBot) -> Iterator[SectionTree]:
    thumbnails = Thumbnails(lambda _id: None)
    hover = RowHover(thumbnails)
    tree = show_tree(
        qtbot,
        WorkspaceBrowserRow(row_id="a", label="front_door", icon_kind="video", thumbnail=OpenStream()),
        WorkspaceBrowserRow(row_id="b", label="Night shift", icon_kind="playlist"),
    )
    hover.watch(tree)
    yield tree
    hover.hide()
    thumbnails.cleanup()


def _rest_on(tree: SectionTree, index: int) -> None:
    widget = row_widget(tree, item_at(tree, index))
    QApplication.sendEvent(widget, QHelpEvent(QEvent.Type.ToolTip, QPoint(4, 4), widget.mapToGlobal(QPoint(4, 4))))


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


def test_the_card_shows_only_the_picture_follows_the_pointer_and_hides_on_leaving_or_scrolling(
    tree: SectionTree,
) -> None:
    _rest_on(tree, 0)
    card = _card(tree)
    assert card is not None
    assert card.isVisible()
    assert not card.findChildren(QLabel)

    inside = row_widget(tree, item_at(tree, 0)).mapToGlobal(QPoint(30, 6))
    _move_to(tree, inside)
    assert card.pos() == inside + QPoint(Space.M, Space.M)

    _move_to(tree, row_widget(tree, item_at(tree, 1)).mapToGlobal(QPoint(4, 4)))
    assert not card.isVisible()

    _rest_on(tree, 0)
    wheel = QWheelEvent(
        QPointF(4, 4), QPointF(4, 4), QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
    )  # fmt: skip
    QApplication.sendEvent(tree.viewport(), wheel)
    assert not card.isVisible()


def test_a_row_without_a_thumbnail_shows_no_card(tree: SectionTree) -> None:
    _rest_on(tree, 1)

    assert _card(tree) is None


def test_a_row_whose_thumbnail_goes_away_gets_no_card_again(tree: SectionTree) -> None:
    _rest_on(tree, 0)
    card = _card(tree)
    assert card is not None
    assert card.isVisible()

    tree.set_sections(
        (
            WorkspaceBrowserSection(
                BrowserSection.PLAYLISTS, (WorkspaceBrowserRow(row_id="a", label="front_door", icon_kind="video"),)
            ),
        )
    )
    _rest_on(tree, 0)

    assert not card.isVisible()
