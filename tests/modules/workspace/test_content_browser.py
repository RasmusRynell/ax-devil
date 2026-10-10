"""Tests for ContentBrowserWidget row rendering and signals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QToolButton, QTreeWidget, QTreeWidgetItem
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace import (
    ConsiderationItemRef,
    FileVideoSourceSpec,
    SeekableVideoContent,
    WorkspaceBrowserRow,
)
from ax_devil.modules.workspace.content import OnScreenWorkspaceItem
from ax_devil.modules.workspace.content_browser import TREE_CONSIDERATION_COLUMN, ContentBrowserWidget
from ax_devil.modules.workspace.item_info import WorkspaceItemInfo
from ax_devil.modules.workspace.item_info_dialog import WorkspaceItemInfoDialog


def _make_video(name: str = "test.mp4") -> SeekableVideoContent:
    return SeekableVideoContent(display_name=name, source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")))


def _browser(qtbot: QtBot, *rows: WorkspaceBrowserRow) -> ContentBrowserWidget:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    browser.set_browser_rows(rows)
    return browser


def _tree(browser: ContentBrowserWidget) -> QTreeWidget:
    tree: QTreeWidget | None = browser.findChild(QTreeWidget)
    assert tree is not None
    return tree


def _search(browser: ContentBrowserWidget) -> QLineEdit:
    search: QLineEdit | None = browser.findChild(QLineEdit)
    assert search is not None
    return search


def _show_excluded_toggle(browser: ContentBrowserWidget) -> QToolButton:
    """Return the list filter button, the only tool button placed directly in the browser toolbar."""
    toggle: QToolButton | None = browser.findChild(QToolButton, options=Qt.FindChildOption.FindDirectChildrenOnly)
    assert toggle is not None
    return toggle


def _row_toggle(browser: ContentBrowserWidget, item: QTreeWidgetItem) -> QToolButton:
    toggle = _tree(browser).itemWidget(item, TREE_CONSIDERATION_COLUMN)
    assert isinstance(toggle, QToolButton)
    return toggle


def _required_top_item(browser: ContentBrowserWidget, index: int = 0) -> QTreeWidgetItem:
    item = _tree(browser).topLevelItem(index)
    assert item is not None
    return item


def _required_child(item: QTreeWidgetItem, index: int) -> QTreeWidgetItem:
    child = item.child(index)
    assert child is not None
    return child


def _context_menu(
    browser: ContentBrowserWidget,
    item: QTreeWidgetItem,
    choose: str | None = None,
    after_choosing: Callable[[], None] | None = None,
) -> dict[str, bool]:
    """Right-click *item*, return its menu entries with their enabled state, and optionally pick one.

    *after_choosing* runs once the chosen action is underway, for closing a dialog it opens.
    """
    browser.show()
    tree = _tree(browser)
    entries: dict[str, bool] = {}

    def inspect_menu() -> None:
        menu = browser.findChildren(QMenu)[-1]
        entries.update({action.text(): action.isEnabled() for action in menu.actions() if not action.isSeparator()})
        menu.close()
        if after_choosing is not None:
            QTimer.singleShot(0, after_choosing)
        if choose is not None:
            next(action for action in menu.actions() if action.text() == choose).trigger()

    QTimer.singleShot(0, inspect_menu)
    tree.customContextMenuRequested.emit(tree.visualItemRect(item).center())
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return entries


def test_renders_explicit_rows(qtbot: QtBot) -> None:
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(
            row_id="row-0",
            label="Suite",
            icon_kind="playlist",
            children=(
                WorkspaceBrowserRow(row_id="row-1", label="Entry 1", icon_kind="video"),
                WorkspaceBrowserRow(row_id="row-2", label="Entry 2", icon_kind="video"),
            ),
        ),
    )

    top_item = _required_top_item(browser)
    assert top_item.text(0) == "Suite"
    assert [_required_child(top_item, index).text(0) for index in range(top_item.childCount())] == [
        "Entry 1",
        "Entry 2",
    ]


def test_refresh_preserves_expansion_by_identity_after_reordering(qtbot: QtBot) -> None:
    child = WorkspaceBrowserRow(row_id="child", label="Entry", icon_kind="video", is_open=True)
    first = WorkspaceBrowserRow(row_id="first", label="Same name", icon_kind="playlist", children=(child,))
    second = WorkspaceBrowserRow(
        row_id="second", label="Same name", icon_kind="playlist", children=(replace(child, row_id="other-child"),)
    )
    browser = _browser(qtbot, first, second)
    _required_top_item(browser, 0).setExpanded(False)
    _required_top_item(browser, 1).setExpanded(True)

    browser.set_browser_rows((second, replace(first, label="Renamed")))

    assert _required_top_item(browser, 0).isExpanded()
    assert not _required_top_item(browser, 1).isExpanded()


def test_open_row_expands_its_parent(qtbot: QtBot) -> None:
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(
            row_id="row-11",
            label="Suite",
            icon_kind="playlist",
            children=(WorkspaceBrowserRow(row_id="row-12", label="Entry 1", icon_kind="video", is_open=True),),
        ),
    )

    assert _required_top_item(browser).isExpanded()


def test_search_reveals_matches_after_refresh(qtbot: QtBot) -> None:
    rows = (
        WorkspaceBrowserRow(
            row_id="playlist",
            label="Playlist",
            icon_kind="playlist",
            children=(WorkspaceBrowserRow(row_id="entry", label="Match", icon_kind="video"),),
        ),
    )
    browser = _browser(qtbot, *rows)
    _search(browser).setText("Match")
    _required_top_item(browser).setExpanded(False)

    browser.set_browser_rows(rows)

    assert _required_top_item(browser).isExpanded()


def test_consideration_toggle_requests_the_change_for_its_row(qtbot: QtBot) -> None:
    item_ref = ConsiderationItemRef.playlist_entry("playlist-id", 0)
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(
            row_id="row-3", label="Entry 1", icon_kind="video", consideration_ref=item_ref, is_considered=False
        ),
    )
    toggle = _row_toggle(browser, _required_top_item(browser))
    assert not toggle.isChecked()

    with qtbot.waitSignal(browser.item_consideration_change_requested, timeout=1000) as blocker:
        toggle.click()

    assert blocker.args == [item_ref, True]


def test_filter_waits_for_workspace_state_after_toggle_request(qtbot: QtBot) -> None:
    row = WorkspaceBrowserRow(
        row_id="entry",
        label="Entry",
        icon_kind="video",
        consideration_ref=ConsiderationItemRef.playlist_entry("playlist", 0),
    )
    browser = _browser(qtbot, row)
    _row_toggle(browser, _required_top_item(browser)).click()
    _search(browser).setText("Entry")
    assert not _required_top_item(browser).isHidden()

    browser.set_browser_rows((replace(row, is_considered=False),))

    assert _required_top_item(browser).isHidden()


def test_excluded_rows_stay_hidden_through_search_until_shown(qtbot: QtBot) -> None:
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(
            row_id="row-10",
            label="Warehouse door",
            icon_kind="video",
            consideration_ref=ConsiderationItemRef.playlist_entry("playlist-id", 0),
            is_considered=False,
        ),
    )
    assert _required_top_item(browser).isHidden()

    _search(browser).setText("warehouse")
    assert _required_top_item(browser).isHidden()

    _show_excluded_toggle(browser).click()
    assert not _required_top_item(browser).isHidden()


def test_search_filters_workspace_rows_by_name_and_location(qtbot: QtBot) -> None:
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(row_id="row-5", label="Parking lot north", icon_kind="video"),
        WorkspaceBrowserRow(
            row_id="row-6",
            label="cam.mp4",
            icon_kind="video",
            location="/data/site_a/cam.mp4",
            location_hint="site_a",
        ),
    )
    search = _search(browser)
    location_row = _required_top_item(browser, 1)
    assert location_row.text(0) == "cam.mp4 — site_a"
    assert location_row.toolTip(0) == "/data/site_a/cam.mp4"

    search.setText("parking")
    assert not _required_top_item(browser, 0).isHidden()
    assert location_row.isHidden()

    search.setText("site_a")
    assert _required_top_item(browser, 0).isHidden()
    assert not location_row.isHidden()

    search.clear()
    assert not _required_top_item(browser, 0).isHidden()
    assert not location_row.isHidden()


def test_search_keeps_matching_playlist_children_reachable(qtbot: QtBot) -> None:
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(
            row_id="row-7",
            label="Daily playlist",
            icon_kind="playlist",
            children=(
                WorkspaceBrowserRow(row_id="row-8", label="Parking lot north", icon_kind="video"),
                WorkspaceBrowserRow(row_id="row-9", label="Warehouse door", icon_kind="video"),
            ),
        ),
    )

    _search(browser).setText("warehouse")

    top_item = _required_top_item(browser)
    assert not top_item.isHidden()
    assert _required_child(top_item, 0).isHidden()
    assert not _required_child(top_item, 1).isHidden()
    assert top_item.isExpanded()


def _double_click(browser: ContentBrowserWidget, item: QTreeWidgetItem, column: int = 0) -> None:
    tree = _tree(browser)
    browser.show()
    position = tree.visualRect(tree.indexFromItem(item, column)).center()
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=position)


def test_double_click_opens_the_row_and_expands_it(qtbot: QtBot) -> None:
    """Double-clicking a collapsed row opens its content and leaves its children visible instead of toggling them."""
    video = _make_video()
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(
            row_id="row-13",
            label="Video",
            icon_kind="video",
            activation_target=(video, 0),
            children=(WorkspaceBrowserRow(row_id="row-14", label="Overlay", icon_kind="overlay"),),
        ),
    )
    top_item = _required_top_item(browser)
    top_item.setExpanded(False)

    with qtbot.waitSignal(browser.content_activated, timeout=1000) as blocker:
        _double_click(browser, top_item)

    assert blocker.args == [video, 0]
    assert top_item.isExpanded()


def test_double_click_consideration_column_does_not_activate(qtbot: QtBot) -> None:
    video = _make_video()
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(
            row_id="row-15",
            label="Video",
            icon_kind="video",
            activation_target=(video, 0),
            consideration_ref=ConsiderationItemRef.video_lane(video.content_id, 0),
        ),
    )
    activated = Mock()
    browser.content_activated.connect(activated)

    _tree(browser).itemDoubleClicked.emit(_required_top_item(browser), TREE_CONSIDERATION_COLUMN)

    activated.assert_not_called()


def test_ctrl_double_click_opens_to_the_side(qtbot: QtBot) -> None:
    video = _make_video()
    browser = _browser(
        qtbot, WorkspaceBrowserRow(row_id="video", label="Video", icon_kind="video", activation_target=(video, 0))
    )
    activated = Mock()
    browser.content_activated.connect(activated)
    tree = _tree(browser)
    browser.show()
    position = tree.visualItemRect(_required_top_item(browser)).center()

    with qtbot.waitSignal(browser.content_open_to_side_requested) as side:
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=position)
        QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=position)

    assert side.args == [video, 0]
    activated.assert_not_called()


def test_context_menu_shows_information_on_request_and_removes_only_top_level_content(qtbot: QtBot) -> None:
    video = _make_video()
    info = WorkspaceItemInfo(title="Video", fields=(("type", "video"),))
    information_factory = Mock(return_value=info)
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(
            row_id="row-16",
            label="Video",
            icon_kind="video",
            removable_content=video,
            information_factory=information_factory,
            children=(
                WorkspaceBrowserRow(
                    row_id="row-17", label="Overlay", icon_kind="overlay", information_factory=information_factory
                ),
            ),
        ),
    )
    top_item = _required_top_item(browser)
    top_item.setExpanded(True)

    assert list(_context_menu(browser, top_item)) == ["Information", "Remove"]
    assert list(_context_menu(browser, _required_child(top_item, 0))) == ["Information"]
    information_factory.assert_not_called()

    shown: list[str] = []

    def close_information() -> None:
        dialog = next(
            widget for widget in QApplication.topLevelWidgets() if isinstance(widget, WorkspaceItemInfoDialog)
        )
        shown.append(dialog.windowTitle())
        dialog.reject()

    _context_menu(browser, top_item, choose="Information", after_choosing=close_information)
    information_factory.assert_called_once_with()
    assert len(shown) == 1

    with qtbot.waitSignal(browser.content_remove_requested) as removed:
        _context_menu(browser, top_item, choose="Remove")
    assert removed.args == [video]


@pytest.mark.parametrize("is_open", [True, False])
def test_export_action_requests_the_open_item(qtbot: QtBot, is_open: bool) -> None:
    target = OnScreenWorkspaceItem(kind="video", content_id="video")
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(row_id="video", label="Video", icon_kind="video", export_target=target, is_open=is_open),
    )
    exported = Mock()
    browser.export_requested.connect(exported)

    [(label, enabled)] = _context_menu(browser, _required_top_item(browser)).items()
    assert enabled is is_open
    if is_open:
        _context_menu(browser, _required_top_item(browser), choose=label)
        exported.assert_called_once_with(target)


def test_context_menus_and_actions_are_disposed_after_closing(qtbot: QtBot) -> None:
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(row_id="video", label="Video", icon_kind="video", removable_content=_make_video()),
    )
    browser.show()
    tree = _tree(browser)
    position = tree.visualItemRect(_required_top_item(browser)).center()
    destroyed = Mock()

    def close_menu() -> None:
        for menu in browser.findChildren(QMenu):
            menu.destroyed.connect(destroyed)
            for action in menu.actions():
                assert action.parent() is menu
                action.destroyed.connect(destroyed)
            menu.close()

    for _ in range(3):
        QTimer.singleShot(0, close_menu)
        tree.customContextMenuRequested.emit(position)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert destroyed.call_count == 6
    assert browser.findChildren(QMenu) == []


def test_open_actions_open_in_preview_or_to_the_side(qtbot: QtBot) -> None:
    video = _make_video()
    browser = _browser(
        qtbot, WorkspaceBrowserRow(row_id="video", label="Video", icon_kind="video", activation_target=(video, 0))
    )
    item = _required_top_item(browser)

    with qtbot.waitSignal(browser.content_open_to_side_requested) as side:
        _context_menu(browser, item, choose="Open to the Side")
    with qtbot.waitSignal(browser.content_activated) as preview:
        _context_menu(browser, item, choose="Open")

    assert side.args == [video, 0]
    assert preview.args == [video, 0]


def test_workspace_item_info_dialog_fits_screen_and_is_resizable(qtbot: QtBot) -> None:
    dialog = WorkspaceItemInfoDialog(
        WorkspaceItemInfo(
            title="Item",
            fields=(("type", "video"), ("path", "/tmp/test.mp4")),
            children=(WorkspaceItemInfo(title="Overlay 1", fields=(("type", "overlay"),)),),
        )
    )
    qtbot.addWidget(dialog)

    dialog.show()

    screen = dialog.screen()
    assert screen is not None
    assert screen.availableGeometry().contains(dialog.frameGeometry())
    assert dialog.maximumWidth() > dialog.width()
    assert dialog.maximumHeight() > dialog.height()
    tree: QTreeWidget | None = dialog.findChild(QTreeWidget)
    assert tree is not None
    top = tree.topLevelItem(0)
    assert tree.topLevelItemCount() == 1 and top is not None
    assert top.text(0) == "Item"
    assert _required_child(top, 2).text(0) == "Overlay 1"
