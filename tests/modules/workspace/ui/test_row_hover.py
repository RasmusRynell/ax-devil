"""Resting the pointer on a row shows its hover card, with a picture when the row has one."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QHelpEvent, QImage, QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.chrome.hover_card import HoverCard
from ax_devil.modules.thumbnails import OpenFrames, Thumbnails
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.row_hover import RowHover
from ax_devil.modules.workspace.ui.section_tree import SectionTree
from tests.helpers.section_tree_rows import item_at, row_widget, show_tree


@dataclass(frozen=True)
class OpenStream:
    """A source whose picture an open viewer already has, so it is never read."""

    fresh_s: ClassVar[float] = 1.0

    def open_frame(self, open_frames: OpenFrames) -> QImage:
        image = QImage(64, 48, QImage.Format.Format_RGB32)
        image.fill(QColor("red"))
        return image

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


def _card(tree: SectionTree) -> HoverCard:
    card = tree.window().findChild(HoverCard)
    assert card is not None
    return card


def _pictures(card: HoverCard) -> list[QLabel]:
    return [label for label in card.findChildren(QLabel) if not label.pixmap().isNull()]


def test_a_row_with_a_thumbnail_shows_it_with_its_name_and_a_row_without_shows_only_text(tree: SectionTree) -> None:
    _rest_on(tree, 0)
    card = _card(tree)

    assert card.isVisible()
    assert "front_door" in [label.text() for label in card.findChildren(QLabel)]
    assert _pictures(card)

    _rest_on(tree, 1)

    assert card.isVisible()
    assert "Night shift" in [label.text() for label in card.findChildren(QLabel)]
    assert not _pictures(card)


def test_scrolling_hides_the_card(tree: SectionTree) -> None:
    _rest_on(tree, 0)
    card = _card(tree)
    assert card.isVisible()

    wheel = QWheelEvent(
        QPointF(4, 4),
        QPointF(4, 4),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(tree.viewport(), wheel)

    assert not card.isVisible()
