"""Entering a row with a thumbnail shows a picture-only hover card, placed once at the pointer."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QGuiApplication, QImage, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel, QWidget
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
    tree: SectionTree

    @staticmethod
    def pos() -> QPoint:
        return FakeCursor.position


def _widget_at(global_pos: QPoint) -> QWidget | None:
    """The widget of the tree at *global_pos*, ignoring whatever else is on the screen."""
    viewport = FakeCursor.tree.viewport()
    local = viewport.mapFromGlobal(global_pos)
    return viewport.childAt(local) or (viewport if viewport.rect().contains(local) else None)


@pytest.fixture()
def hides(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What hid the card, with the application state then, for the message of a card that never showed."""
    reasons: list[str] = []
    hide = RowHover.hide

    def recording_hide(self: RowHover) -> None:
        caller = sys._getframe(1)
        event = caller.f_locals.get("kind")
        reasons.append(f"{caller.f_code.co_name}({event}) while {QGuiApplication.applicationState().name}")
        hide(self)

    monkeypatch.setattr(RowHover, "hide", recording_hide)
    return reasons


@pytest.fixture()
def tree(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, hides: list[str]) -> Iterator[SectionTree]:
    monkeypatch.setattr(row_hover, "INTENT_MS", 0)
    monkeypatch.setattr(row_hover, "_REFRESH_MS", 20)
    monkeypatch.setattr(row_hover, "QCursor", FakeCursor)
    monkeypatch.setattr(row_hover, "_widget_at", _widget_at)  # Not the real screen, where other windows can be on top.
    thumbnails = Thumbnails(lambda _id: None)
    hover = RowHover(thumbnails)
    tree = show_tree(
        qtbot,
        WorkspaceBrowserRow(row_id="a", label="front_door", icon_kind="video", thumbnail=OpenStream()),
        WorkspaceBrowserRow(row_id="b", label="Night shift", icon_kind="playlist"),
        WorkspaceBrowserRow(row_id="c", label="garage", icon_kind="video", thumbnail=OpenStream()),
    )
    FakeCursor.tree = tree
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


def _visible_card(qtbot: QtBot, tree: SectionTree, hides: list[str]) -> HoverCard:
    def shown() -> None:
        card = _card(tree)
        assert card is not None and card.isVisible(), f"no visible card; hidden by {hides}"

    qtbot.waitUntil(shown)
    card = _card(tree)
    assert card is not None
    return card


def test_the_card_shows_only_the_picture_stays_where_it_was_placed_and_hides_on_leaving_or_scrolling(
    qtbot: QtBot, tree: SectionTree, hides: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    placed: list[QPoint] = []
    show_at = HoverCard.show_at  # The window system may move a window, so what is checked is where it was placed.

    def recording_show_at(card: HoverCard, pos: QPoint) -> None:
        placed.append(pos)
        show_at(card, pos)

    monkeypatch.setattr(HoverCard, "show_at", recording_show_at)

    entered = _enter(tree, 0)
    card = _visible_card(qtbot, tree, hides)
    assert not card.findChildren(QLabel)
    assert placed == [entered]

    _move_to(tree, row_widget(tree, item_at(tree, 0)).mapToGlobal(QPoint(30, 6)))
    assert placed == [entered]

    entered = _enter(tree, 2, QPoint(20, 5))
    assert placed[-1] == entered

    _enter(tree, 1)
    assert not card.isVisible()

    _enter(tree, 0)
    _visible_card(qtbot, tree, hides)
    wheel = QWheelEvent(
        QPointF(4, 4), QPointF(4, 4), QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
    )  # fmt: skip
    QApplication.sendEvent(tree.viewport(), wheel)
    assert not card.isVisible()


def test_a_row_without_a_thumbnail_shows_no_card(tree: SectionTree) -> None:
    _enter(tree, 1)

    assert _card(tree) is None


def test_a_row_whose_thumbnail_goes_away_gets_no_card_again(qtbot: QtBot, tree: SectionTree, hides: list[str]) -> None:
    _enter(tree, 0)
    card = _visible_card(qtbot, tree, hides)

    tree.set_sections(
        (
            WorkspaceBrowserSection(
                BrowserSection.PLAYLISTS, (WorkspaceBrowserRow(row_id="a", label="front_door", icon_kind="video"),)
            ),
        )
    )
    _enter(tree, 0)

    assert not card.isVisible()


def test_the_card_hides_when_the_pointer_leaves_the_row_for_the_empty_viewport(
    qtbot: QtBot, tree: SectionTree, hides: list[str]
) -> None:
    _enter(tree, 0)
    card = _visible_card(qtbot, tree, hides)

    FakeCursor.position = tree.viewport().mapToGlobal(QPoint(10, tree.viewport().height() - 10))  # No event is sent.

    qtbot.waitUntil(lambda: not card.isVisible())
