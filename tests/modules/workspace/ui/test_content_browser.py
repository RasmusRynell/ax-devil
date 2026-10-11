"""Tests for ContentBrowserWidget row rendering and signals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QToolButton, QTreeWidget, QTreeWidgetItem
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.core import (
    ConsiderationItemRef,
    FileVideoSourceSpec,
    SeekableVideoContent,
    UnreadableItem,
    VideoItem,
)
from ax_devil.modules.workspace.core.content import OnScreenWorkspaceItem
from ax_devil.modules.workspace.core.item_info import WorkspaceItemInfo
from ax_devil.modules.workspace.ui.browser_rows import BrowserSection, WorkspaceBrowserRow, WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.content_browser import (
    TREE_CONSIDERATION_COLUMN,
    TREE_HIDDEN_COUNT_ROLE,
    TREE_LABEL_COLUMN,
    TREE_ROW_ROLE,
    ContentBrowserWidget,
)
from ax_devil.modules.workspace.ui.item_info_dialog import WorkspaceItemInfoDialog
from ax_devil.modules.workspace.ui.rename_dialog import RenameDialog
from tests.helpers.widgets import required_child


def _make_video(name: str = "test.mp4") -> SeekableVideoContent:
    return SeekableVideoContent(display_name=name, source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")))


def _browser(qtbot: QtBot, *rows: WorkspaceBrowserRow) -> ContentBrowserWidget:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    _set_rows(browser, rows)
    return browser


def _set_rows(browser: ContentBrowserWidget, rows: tuple[WorkspaceBrowserRow, ...]) -> None:
    browser.set_browser_sections((WorkspaceBrowserSection(BrowserSection.VIDEOS, rows),))


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


def _required_item_row(browser: ContentBrowserWidget, index: int = 0) -> QTreeWidgetItem:
    section = _tree(browser).topLevelItem(0)
    assert section is not None
    return required_child(section, index)


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

    top_item = _required_item_row(browser)
    assert top_item.text(0) == "Suite"
    assert [required_child(top_item, index).text(0) for index in range(top_item.childCount())] == [
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
    _required_item_row(browser, 0).setExpanded(False)
    _required_item_row(browser, 1).setExpanded(True)

    _set_rows(browser, (second, replace(first, label="Renamed")))

    assert _required_item_row(browser, 0).isExpanded()
    assert not _required_item_row(browser, 1).isExpanded()


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

    top_item = _required_item_row(browser)
    assert top_item.isExpanded()
    child_item = required_child(top_item, 0)
    assert child_item.data(TREE_LABEL_COLUMN, TREE_ROW_ROLE).is_open
    assert "Open in a viewer" in child_item.toolTip(0)


def test_a_section_row_is_not_selectable_or_openable_and_is_not_searchable(qtbot: QtBot) -> None:
    browser = _browser(qtbot, WorkspaceBrowserRow(row_id="clip", label="clip.mp4", icon_kind="video"))
    section = _tree(browser).topLevelItem(0)
    assert section is not None

    assert section.isExpanded()
    assert not section.flags() & Qt.ItemFlag.ItemIsSelectable
    assert section.toolTip(0) == ""
    activated = Mock()
    browser.content_activated.connect(activated)
    _tree(browser).itemDoubleClicked.emit(section, 0)
    activated.assert_not_called()
    assert required_child(section, 0).text(0) == "clip.mp4"

    _search(browser).setText("videos")

    assert section.isHidden()
    assert required_child(section, 0).isHidden()

    _search(browser).setText("clip")

    assert not section.isHidden()
    assert not required_child(section, 0).isHidden()


def test_tree_items_expose_their_detail_as_accessible_text(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    browser.set_browser_sections(
        (
            WorkspaceBrowserSection(
                BrowserSection.LIVE,
                (
                    WorkspaceBrowserRow(row_id="a", label="Entrance", icon_kind="live_video", detail="192.0.2.10"),
                    WorkspaceBrowserRow(row_id="b", label="Plain", icon_kind="live_video"),
                ),
            ),
        )
    )
    section = _tree(browser).topLevelItem(0)
    assert section is not None

    def accessible_text(item: QTreeWidgetItem) -> object:
        return item.data(TREE_LABEL_COLUMN, Qt.ItemDataRole.AccessibleTextRole)

    assert accessible_text(section) == "Live, 2"
    assert accessible_text(required_child(section, 0)) == "Entrance, 192.0.2.10"
    assert accessible_text(required_child(section, 1)) == "Plain"


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
    _required_item_row(browser).setExpanded(False)

    _set_rows(browser, rows)

    assert _required_item_row(browser).isExpanded()


def test_consideration_toggle_requests_the_change_for_its_row(qtbot: QtBot) -> None:
    item_ref = ConsiderationItemRef.playlist_entry("playlist-id", 0)
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(
            row_id="row-3", label="Entry 1", icon_kind="video", consideration_ref=item_ref, is_considered=False
        ),
    )
    toggle = _row_toggle(browser, _required_item_row(browser))
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
    _row_toggle(browser, _required_item_row(browser)).click()
    _search(browser).setText("Entry")
    assert not _required_item_row(browser).isHidden()

    _set_rows(browser, (replace(row, is_considered=False),))

    assert _required_item_row(browser).isHidden()


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
    assert _required_item_row(browser).isHidden()

    _search(browser).setText("warehouse")
    assert _required_item_row(browser).isHidden()

    _show_excluded_toggle(browser).click()
    assert not _required_item_row(browser).isHidden()


def test_search_filters_workspace_rows_by_name_and_location(qtbot: QtBot) -> None:
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(row_id="row-5", label="Parking lot north", icon_kind="video"),
        WorkspaceBrowserRow(
            row_id="row-6",
            label="cam.mp4",
            icon_kind="video",
            location="/data/site_a/cam.mp4",
            detail="site_a",
        ),
    )
    search = _search(browser)
    location_row = _required_item_row(browser, 1)
    assert location_row.text(0) == "cam.mp4"
    assert "/data/site_a/cam.mp4" in location_row.toolTip(0)

    search.setText("parking")
    assert not _required_item_row(browser, 0).isHidden()
    assert location_row.isHidden()

    search.setText("site_a")
    assert _required_item_row(browser, 0).isHidden()
    assert not location_row.isHidden()

    search.clear()
    assert not _required_item_row(browser, 0).isHidden()
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

    top_item = _required_item_row(browser)
    assert not top_item.isHidden()
    assert required_child(top_item, 0).isHidden()
    assert not required_child(top_item, 1).isHidden()
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
    top_item = _required_item_row(browser)
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

    _tree(browser).itemDoubleClicked.emit(_required_item_row(browser), TREE_CONSIDERATION_COLUMN)

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
    position = tree.visualItemRect(_required_item_row(browser)).center()

    with qtbot.waitSignal(browser.content_open_to_side_requested) as side:
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=position)
        QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=position)

    assert side.args == [video, 0]
    activated.assert_not_called()


def test_context_menu_shows_information_on_request_and_removes_only_top_level_items(qtbot: QtBot) -> None:
    item = VideoItem(video=Path("/tmp/video.mp4"))
    info = WorkspaceItemInfo(title="Video", fields=(("type", "video"),))
    information_factory = Mock(return_value=info)
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(
            row_id="row-16",
            label="Video",
            icon_kind="video",
            item=item,
            information_factory=information_factory,
            children=(
                WorkspaceBrowserRow(
                    row_id="row-17", label="Overlay", icon_kind="overlay", information_factory=information_factory
                ),
            ),
        ),
    )
    top_item = _required_item_row(browser)
    top_item.setExpanded(True)

    assert list(_context_menu(browser, top_item)) == ["Information", "Rename", "Remove"]
    assert list(_context_menu(browser, required_child(top_item, 0))) == ["Information"]
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

    with qtbot.waitSignal(browser.item_remove_requested) as removed:
        _context_menu(browser, top_item, choose="Remove")
    assert removed.args == [item.id]


@pytest.mark.parametrize("is_open", [True, False])
def test_export_action_requests_the_open_item(qtbot: QtBot, is_open: bool) -> None:
    target = OnScreenWorkspaceItem(kind="video", content_id="video")
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(row_id="video", label="Video", icon_kind="video", export_target=target, is_open=is_open),
    )
    exported = Mock()
    browser.export_requested.connect(exported)

    [(label, enabled)] = _context_menu(browser, _required_item_row(browser)).items()
    assert enabled is is_open
    if is_open:
        _context_menu(browser, _required_item_row(browser), choose=label)
        exported.assert_called_once_with(target)


def test_context_menus_and_actions_are_disposed_after_closing(qtbot: QtBot) -> None:
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(
            row_id="video", label="Video", icon_kind="video", item=VideoItem(video=Path("/tmp/video.mp4"))
        ),
    )
    browser.show()
    tree = _tree(browser)
    position = tree.visualItemRect(_required_item_row(browser)).center()
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

    assert destroyed.call_count == 9  # Each time: the menu, Rename, and Remove.
    assert browser.findChildren(QMenu) == []


def test_open_actions_open_in_preview_or_to_the_side(qtbot: QtBot) -> None:
    video = _make_video()
    browser = _browser(
        qtbot, WorkspaceBrowserRow(row_id="video", label="Video", icon_kind="video", activation_target=(video, 0))
    )
    item = _required_item_row(browser)

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
    assert required_child(top, 2).text(0) == "Overlay 1"


def test_an_unavailable_row_shows_its_reason_instead_of_opening(qtbot: QtBot) -> None:
    item = VideoItem(video=Path("/missing/lot.mp4"))
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(
            row_id=item.id,
            label="lot.mp4",
            icon_kind="unavailable",
            item=item,
            unavailable_reason="File not found: /missing/lot.mp4",
        ),
    )
    activated: list[object] = []
    browser.content_activated.connect(lambda content, index: activated.append(content))
    tree_item = _required_item_row(browser)

    with patch("ax_devil.modules.workspace.ui.content_browser.QMessageBox.warning") as warning:
        _tree(browser).itemDoubleClicked.emit(tree_item, TREE_LABEL_COLUMN)

    assert activated == []
    [(_, _, message)] = [call.args for call in warning.call_args_list]
    assert message == "lot.mp4: File not found: /missing/lot.mp4"
    assert "File not found: /missing/lot.mp4" in tree_item.toolTip(TREE_LABEL_COLUMN)
    assert list(_context_menu(browser, tree_item)) == ["Rename", "Remove"]
    with qtbot.waitSignal(browser.item_remove_requested) as removed:
        _context_menu(browser, tree_item, choose="Remove")
    assert removed.args == [item.id]


def test_rename_asks_for_a_name_and_requests_it(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    item = VideoItem(label="Gate", video=Path("/tmp/gate.mp4"))
    browser = _browser(qtbot, WorkspaceBrowserRow(row_id="gate", label="Gate", icon_kind="video", item=item))
    shown: list[str] = []

    def enter_name(dialog: RenameDialog) -> int:
        shown.append(dialog._name_edit.text())
        dialog._name_edit.setText("  North gate ")
        return RenameDialog.DialogCode.Accepted

    monkeypatch.setattr(RenameDialog, "exec", enter_name)
    with qtbot.waitSignal(browser.item_rename_requested) as renamed:
        _context_menu(browser, _required_item_row(browser), choose="Rename")

    assert shown == ["Gate"]
    assert renamed.args == [item.id, "North gate"]


def test_renaming_an_unlabeled_row_seeds_the_shown_name_and_keeping_it_clears_the_label(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = VideoItem(video=Path("/tmp/gate.mp4"))
    shown_label = "Lot / gate.mp4"
    browser = _browser(qtbot, WorkspaceBrowserRow(row_id="gate", label=shown_label, icon_kind="video", item=item))
    entered: list[str] = []
    shown: list[str] = []

    def enter_name(dialog: RenameDialog) -> int:
        shown.append(dialog._name_edit.text())
        dialog._name_edit.setText(entered.pop(0))
        return RenameDialog.DialogCode.Accepted

    monkeypatch.setattr(RenameDialog, "exec", enter_name)
    for name, expected in ((shown_label, ""), ("North gate", "North gate")):
        entered.append(name)
        with qtbot.waitSignal(browser.item_rename_requested) as renamed:
            _context_menu(browser, _required_item_row(browser), choose="Rename")
        assert renamed.args == [item.id, expected]

    assert shown == [shown_label, shown_label]


def test_an_unreadable_item_cannot_be_renamed(qtbot: QtBot) -> None:
    item = UnreadableItem.from_raw({"kind": "radar", "id": "r1"}, "Unknown item kind: 'radar'.")
    browser = _browser(
        qtbot,
        WorkspaceBrowserRow(
            row_id=item.id, label="radar", icon_kind="unavailable", item=item, unavailable_reason=item.reason
        ),
    )

    assert _context_menu(browser, _required_item_row(browser)) == {"Rename": False, "Remove": True}


def test_clicking_a_section_folds_it_and_the_fold_survives_a_refresh(qtbot: QtBot) -> None:
    row = WorkspaceBrowserRow(row_id="clip", label="clip.mp4", icon_kind="video")
    browser = _browser(qtbot, row)
    section = _tree(browser).topLevelItem(0)
    assert section is not None

    _tree(browser).itemClicked.emit(section, 0)

    assert not section.isExpanded()

    _set_rows(browser, (row,))

    folded = _tree(browser).topLevelItem(0)
    assert folded is not None
    assert not folded.isExpanded()

    _tree(browser).itemClicked.emit(folded, 0)

    assert folded.isExpanded()


def test_a_folded_section_unfolds_for_a_search_and_folds_again_after_it(qtbot: QtBot) -> None:
    row = WorkspaceBrowserRow(row_id="clip", label="clip.mp4", icon_kind="video")
    browser = _browser(qtbot, row)
    section = _tree(browser).topLevelItem(0)
    assert section is not None
    section.setExpanded(False)

    _search(browser).setText("clip")

    assert section.isExpanded()

    _set_rows(browser, (row,))
    _search(browser).clear()

    refolded = _tree(browser).topLevelItem(0)
    assert refolded is not None
    assert not refolded.isExpanded()


def test_a_folded_section_unfolds_for_an_open_row(qtbot: QtBot) -> None:
    row = WorkspaceBrowserRow(row_id="clip", label="clip.mp4", icon_kind="video")
    browser = _browser(qtbot, row)
    section = _tree(browser).topLevelItem(0)
    assert section is not None
    section.setExpanded(False)

    _set_rows(browser, (replace(row, is_open=True),))

    reopened = _tree(browser).topLevelItem(0)
    assert reopened is not None
    assert reopened.isExpanded()


def test_a_folded_section_folds_again_when_its_open_row_closes(qtbot: QtBot) -> None:
    row = WorkspaceBrowserRow(row_id="clip", label="clip.mp4", icon_kind="video")
    browser = _browser(qtbot, row)
    section = _tree(browser).topLevelItem(0)
    assert section is not None
    section.setExpanded(False)

    _set_rows(browser, (replace(row, is_open=True),))
    _set_rows(browser, (replace(row, is_open=True),))

    kept_open = _tree(browser).topLevelItem(0)
    assert kept_open is not None
    assert kept_open.isExpanded()

    _set_rows(browser, (row,))

    closed = _tree(browser).topLevelItem(0)
    assert closed is not None
    assert not closed.isExpanded()


def test_a_search_matching_only_an_excluded_item_keeps_its_section_listed(qtbot: QtBot) -> None:
    excluded = WorkspaceBrowserRow(
        row_id="entry",
        label="Entry",
        icon_kind="video",
        consideration_ref=ConsiderationItemRef.playlist_entry("playlist-id", 0),
        is_considered=False,
    )
    browser = _browser(qtbot, excluded)
    section = _tree(browser).topLevelItem(0)
    assert section is not None

    _search(browser).setText("entry")

    assert not section.isHidden()
    assert _required_item_row(browser).isHidden()


def test_a_section_counts_the_excluded_items_the_list_hides(qtbot: QtBot) -> None:
    excluded = WorkspaceBrowserRow(
        row_id="entry",
        label="Entry",
        icon_kind="video",
        consideration_ref=ConsiderationItemRef.playlist_entry("playlist-id", 0),
        is_considered=False,
    )
    browser = _browser(qtbot, WorkspaceBrowserRow(row_id="clip", label="clip.mp4", icon_kind="video"), excluded)
    section = _tree(browser).topLevelItem(0)
    assert section is not None

    assert section.data(TREE_LABEL_COLUMN, TREE_HIDDEN_COUNT_ROLE) == 1

    _show_excluded_toggle(browser).click()

    assert section.data(TREE_LABEL_COLUMN, TREE_HIDDEN_COUNT_ROLE) == 0


def test_a_section_counts_only_excluded_items_the_search_would_otherwise_show(qtbot: QtBot) -> None:
    excluded = WorkspaceBrowserRow(
        row_id="entry",
        label="Entry",
        icon_kind="video",
        consideration_ref=ConsiderationItemRef.playlist_entry("playlist-id", 0),
        is_considered=False,
    )
    browser = _browser(qtbot, WorkspaceBrowserRow(row_id="clip", label="clip.mp4", icon_kind="video"), excluded)
    section = _tree(browser).topLevelItem(0)
    assert section is not None

    _search(browser).setText("clip")

    assert section.data(TREE_LABEL_COLUMN, TREE_HIDDEN_COUNT_ROLE) == 0

    _search(browser).setText("entry")

    assert section.data(TREE_LABEL_COLUMN, TREE_HIDDEN_COUNT_ROLE) == 1
    assert section.data(TREE_LABEL_COLUMN, Qt.ItemDataRole.AccessibleTextRole) == "Videos, 2, 1 hidden"
