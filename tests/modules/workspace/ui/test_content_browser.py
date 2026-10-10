"""Tests for ContentBrowserWidget row rendering and signals."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import NoReturn
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu, QToolButton, QTreeWidget, QTreeWidgetItem
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.workspace.core import (
    ConsiderationItemRef,
    FileVideoSourceSpec,
    SeekableVideoContent,
    UnreadableItem,
    VideoItem,
)
from ax_devil.modules.workspace.core.content import OnScreenWorkspaceItem
from ax_devil.modules.workspace.core.item_info import WorkspaceItemInfo
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.content_browser import (
    TREE_CONSIDERATION_COLUMN,
    TREE_INDENTATION_PX,
    TREE_LABEL_COLUMN,
    ContentBrowserWidget,
)
from ax_devil.modules.workspace.ui.item_info_dialog import WorkspaceItemInfoDialog
from ax_devil.modules.workspace.ui.rename_dialog import RenameDialog


def _noop() -> NoReturn:
    raise NotImplementedError("stub")


def _make_video(name: str = "test.mp4") -> SeekableVideoContent:
    return SeekableVideoContent(display_name=name, source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")))


def _required_top_item(browser: ContentBrowserWidget, index: int = 0) -> QTreeWidgetItem:
    item: QTreeWidgetItem | None = browser._tree.topLevelItem(index)
    assert item is not None
    return item


def _required_tree_top_item(tree: QTreeWidget, index: int = 0) -> QTreeWidgetItem:
    item = tree.topLevelItem(index)
    assert item is not None
    return item


def _required_child(item: QTreeWidgetItem, index: int) -> QTreeWidgetItem:
    child = item.child(index)
    assert child is not None
    return child


def test_renders_explicit_rows(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)

    browser.set_browser_rows(
        (
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
    )

    top_item = _required_top_item(browser)
    assert browser._tree.indentation() == TREE_INDENTATION_PX
    assert browser._tree.columnCount() == 2
    assert top_item.text(0) == "Suite"
    assert top_item.childCount() == 2
    assert _required_child(top_item, 0).text(0) == "Entry 1"
    assert _required_child(top_item, 1).text(0) == "Entry 2"


def test_tree_does_not_toggle_expansion_on_double_click(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)

    assert not browser._tree.expandsOnDoubleClick()


def test_refresh_preserves_expansion_by_identity_after_reordering(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    child = WorkspaceBrowserRow(row_id="child", label="Entry", icon_kind="video", is_open=True)
    first = WorkspaceBrowserRow(row_id="first", label="Same name", icon_kind="playlist", children=(child,))
    second = WorkspaceBrowserRow(
        row_id="second", label="Same name", icon_kind="playlist", children=(replace(child, row_id="other-child"),)
    )
    browser.set_browser_rows((first, second))
    _required_top_item(browser, 0).setExpanded(False)
    _required_top_item(browser, 1).setExpanded(True)

    browser.set_browser_rows((second, replace(first, label="Renamed")))

    assert _required_top_item(browser, 0).isExpanded()
    assert not _required_top_item(browser, 1).isExpanded()
    assert _required_child(_required_top_item(browser, 1), 0).font(0).bold()


def test_search_reveals_matches_after_refresh(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    rows = (
        WorkspaceBrowserRow(
            row_id="playlist",
            label="Playlist",
            icon_kind="playlist",
            children=(WorkspaceBrowserRow(row_id="entry", label="Match", icon_kind="video"),),
        ),
    )
    browser.set_browser_rows(rows)
    browser._search_edit.setText("Match")
    _required_top_item(browser).setExpanded(False)

    browser.set_browser_rows(rows)

    assert _required_top_item(browser).isExpanded()


def test_consideration_toggle_emits_requested_ref_and_styles_row(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    item_ref = ConsiderationItemRef.playlist_entry("playlist-id", 0)
    browser.set_browser_rows(
        (
            WorkspaceBrowserRow(
                row_id="row-3",
                label="Entry 1",
                icon_kind="video",
                consideration_ref=item_ref,
                is_considered=False,
            ),
        )
    )

    item = _required_top_item(browser)
    toggle = browser._tree.itemWidget(item, TREE_CONSIDERATION_COLUMN)
    assert isinstance(toggle, QToolButton)
    assert not toggle.isChecked()
    expected = browser.palette().color(QPalette.ColorRole.Text)
    expected.setAlphaF(0.5)
    assert item.foreground(0).color() == expected

    with qtbot.waitSignal(browser.item_consideration_change_requested, timeout=1000) as blocker:
        toggle.click()

    assert blocker.args == [item_ref, True]


def test_filter_waits_for_workspace_state_after_toggle_request(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    row = WorkspaceBrowserRow(
        row_id="entry",
        label="Entry",
        icon_kind="video",
        consideration_ref=ConsiderationItemRef.playlist_entry("playlist", 0),
    )
    browser.set_browser_rows((row,))
    toggle = browser._tree.itemWidget(_required_top_item(browser), TREE_CONSIDERATION_COLUMN)
    assert isinstance(toggle, QToolButton)
    toggle.click()
    browser._search_edit.setText("Entry")
    assert not _required_top_item(browser).isHidden()

    browser.set_browser_rows((replace(row, is_considered=False),))

    assert _required_top_item(browser).isHidden()


def test_palette_change_refreshes_existing_row_colors(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    browser.set_browser_rows(
        (
            WorkspaceBrowserRow(
                row_id="video",
                label="Video",
                icon_kind="video",
                children=(
                    WorkspaceBrowserRow(row_id="overlay", label="Overlay", icon_kind="overlay", is_considered=False),
                ),
            ),
        )
    )
    top = _required_top_item(browser)
    child = _required_child(top, 0)
    for color in (QColor("#123456"), QColor("#abcdef")):
        palette = browser.palette()
        palette.setColor(QPalette.ColorRole.Text, color)
        browser.setPalette(palette)
        QCoreApplication.processEvents()  # Restyling runs on the next event-loop turn.
        assert top.foreground(0).color() == color
        excluded_color = QColor(color)
        excluded_color.setAlphaF(0.5)
        assert child.foreground(0).color() == excluded_color


def test_excluded_visibility_toggle_hides_and_shows_unconsidered_rows(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    browser.set_browser_rows(
        (
            WorkspaceBrowserRow(
                row_id="row-4",
                label="Entry 1",
                icon_kind="video",
                consideration_ref=ConsiderationItemRef.playlist_entry("playlist-id", 0),
                is_considered=False,
            ),
        )
    )

    item = _required_top_item(browser)
    assert item.isHidden()

    browser._show_excluded_toggle.click()

    assert not item.isHidden()


def test_search_filters_workspace_rows_by_name(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    browser.set_browser_rows(
        (
            WorkspaceBrowserRow(row_id="row-5", label="Parking lot north", icon_kind="video"),
            WorkspaceBrowserRow(row_id="row-6", label="Warehouse door", icon_kind="video"),
        )
    )

    browser._search_edit.setText("parking")

    assert not _required_top_item(browser, 0).isHidden()
    assert _required_top_item(browser, 1).isHidden()

    browser._search_edit.clear()

    assert not _required_top_item(browser, 0).isHidden()
    assert not _required_top_item(browser, 1).isHidden()


def test_search_keeps_matching_playlist_children_reachable(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    browser.set_browser_rows(
        (
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
    )

    browser._search_edit.setText("warehouse")

    top_item = _required_top_item(browser)
    first_child = _required_child(top_item, 0)
    second_child = _required_child(top_item, 1)

    assert not top_item.isHidden()
    assert first_child.isHidden()
    assert not second_child.isHidden()
    assert top_item.isExpanded()


def test_search_preserves_hidden_excluded_rows(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    browser.set_browser_rows(
        (
            WorkspaceBrowserRow(
                row_id="row-10",
                label="Warehouse door",
                icon_kind="video",
                consideration_ref=ConsiderationItemRef.playlist_entry("playlist-id", 0),
                is_considered=False,
            ),
        )
    )

    browser._search_edit.setText("warehouse")

    assert _required_top_item(browser).isHidden()

    browser._show_excluded_toggle.click()

    assert not _required_top_item(browser).isHidden()


def test_open_row_is_bold_and_expanded(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    browser.set_browser_rows(
        (
            WorkspaceBrowserRow(
                row_id="row-11",
                label="Suite",
                icon_kind="playlist",
                children=(WorkspaceBrowserRow(row_id="row-12", label="Entry 1", icon_kind="video", is_open=True),),
            ),
        )
    )

    top_item = _required_top_item(browser)
    child_item = _required_child(top_item, 0)

    assert top_item.isExpanded()
    assert child_item.font(0).bold()
    assert child_item.toolTip(0) == "Open in at least one viewer"


def test_double_click_emits_activation_target_and_expands_path(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    video = _make_video()
    browser.set_browser_rows(
        (
            WorkspaceBrowserRow(
                row_id="row-13",
                label="Video",
                icon_kind="video",
                children=(
                    WorkspaceBrowserRow(
                        row_id="row-14", label="Overlay", icon_kind="overlay", activation_target=(video, 0)
                    ),
                ),
            ),
        )
    )
    top_item = _required_top_item(browser)
    child_item = _required_child(top_item, 0)
    top_item.setExpanded(False)

    with qtbot.waitSignal(browser.content_activated, timeout=1000) as blocker:
        browser._tree.itemDoubleClicked.emit(child_item, 0)

    assert blocker.args == [video, 0]
    assert top_item.isExpanded()


def test_double_click_consideration_column_does_not_activate(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    video = _make_video()
    browser.set_browser_rows(
        (WorkspaceBrowserRow(row_id="row-15", label="Video", icon_kind="video", activation_target=(video, 0)),)
    )
    activated: list[object] = []
    browser.content_activated.connect(lambda content, index: activated.extend([content, index]))

    browser._tree.itemDoubleClicked.emit(_required_top_item(browser), TREE_CONSIDERATION_COLUMN)

    assert activated == []


def test_context_menu_uses_explicit_information_and_removal_payload(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    info = WorkspaceItemInfo(title="Video", fields=(("type", "video"),))
    information_factory = Mock(return_value=info)
    browser.set_browser_rows(
        (
            WorkspaceBrowserRow(
                row_id="row-16",
                label="Video",
                icon_kind="video",
                item=VideoItem(video=Path("/tmp/video.mp4")),
                information_factory=information_factory,
                children=(
                    WorkspaceBrowserRow(
                        row_id="row-17",
                        label="Overlay",
                        icon_kind="overlay",
                        information_factory=information_factory,
                    ),
                ),
            ),
        )
    )

    top_menu = browser._build_context_menu(_required_top_item(browser))
    child_menu = browser._build_context_menu(_required_child(_required_top_item(browser), 0))

    assert [action.text() for action in top_menu.actions()] == ["Information", "Rename", "Remove"]
    assert [action.text() for action in child_menu.actions()] == ["Information"]
    information_factory.assert_not_called()

    with patch.object(browser, "_show_item_information") as show_information:
        top_menu.actions()[0].trigger()

    information_factory.assert_called_once_with()
    show_information.assert_called_once_with(info)


@pytest.mark.parametrize("is_open", [True, False])
def test_export_action_requests_the_open_item(qtbot: QtBot, is_open: bool) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    target = OnScreenWorkspaceItem(kind="video", content_id="video")
    browser.set_browser_rows(
        (WorkspaceBrowserRow(row_id="video", label="Video", icon_kind="video", export_target=target, is_open=is_open),)
    )
    (action,) = browser._build_context_menu(_required_top_item(browser)).actions()
    assert action.isEnabled() is is_open
    if is_open:
        with qtbot.waitSignal(browser.export_requested) as emitted:
            action.trigger()
        assert emitted.args == [target]


def test_context_menus_and_actions_are_disposed_after_closing(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    browser.set_browser_rows(
        (
            WorkspaceBrowserRow(
                row_id="video",
                label="Video",
                icon_kind="video",
                item=VideoItem(video=Path("/tmp/video.mp4")),
            ),
        )
    )
    browser.show()
    position = browser._tree.visualItemRect(_required_top_item(browser)).center()
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
        browser._show_context_menu(position)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert destroyed.call_count == 9  # Each time: the menu, Rename, and Remove.
    assert browser.findChildren(QMenu) == []


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
    assert dialog._tree.topLevelItemCount() == 1
    assert _required_tree_top_item(dialog._tree).text(0) == "Item"
    assert _required_child(_required_tree_top_item(dialog._tree), 2).text(0) == "Overlay 1"


def test_open_actions_open_in_preview_or_to_the_side(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    video = _make_video()
    browser.set_browser_rows(
        (WorkspaceBrowserRow(row_id="video", label="Video", icon_kind="video", activation_target=(video, 0)),)
    )
    menu = browser._build_context_menu(_required_top_item(browser))
    actions = {action.text(): action for action in menu.actions() if not action.isSeparator()}

    with qtbot.waitSignal(browser.content_open_to_side_requested) as side:
        actions["Open to the Side"].trigger()
    with qtbot.waitSignal(browser.content_activated) as preview:
        actions["Open"].trigger()

    assert side.args == [video, 0]
    assert preview.args == [video, 0]


def test_ctrl_double_click_opens_to_the_side(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    video = _make_video()
    browser.set_browser_rows(
        (WorkspaceBrowserRow(row_id="video", label="Video", icon_kind="video", activation_target=(video, 0)),)
    )
    activated = Mock()
    browser.content_activated.connect(activated)
    browser.show()
    viewport = browser._tree.viewport()
    position = browser._tree.visualItemRect(_required_top_item(browser)).center()

    with qtbot.waitSignal(browser.content_open_to_side_requested) as side:
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=position)
        QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=position)

    assert side.args == [video, 0]
    activated.assert_not_called()


def test_rows_show_location_hints_with_middle_elision_and_path_tooltips(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    browser.set_browser_rows(
        (
            WorkspaceBrowserRow(
                row_id="a",
                label="cam.mp4",
                icon_kind="video",
                location="/data/site_a/cam.mp4",
                location_hint="site_a",
            ),
        )
    )
    item = _required_top_item(browser)

    assert browser._tree.textElideMode() == Qt.TextElideMode.ElideMiddle
    assert item.text(0) == "cam.mp4 — site_a"
    assert item.toolTip(0) == "/data/site_a/cam.mp4"
    browser._search_edit.setText("site_a")
    assert not item.isHidden()


def test_excluded_filter_and_row_toggle_use_different_icons_and_tooltips(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    browser.set_browser_rows(
        (
            WorkspaceBrowserRow(
                row_id="lane",
                label="Lane",
                icon_kind="overlay",
                consideration_ref=ConsiderationItemRef.video_lane("video", 0),
            ),
        )
    )
    row_toggle = browser._tree.itemWidget(_required_top_item(browser), TREE_CONSIDERATION_COLUMN)
    assert isinstance(row_toggle, QToolButton)
    filter_image = browser._show_excluded_toggle.icon().pixmap(16, 16).toImage()
    eye_images = (
        Icon.SHOWN.icon().pixmap(16, 16).toImage(),
        Icon.HIDDEN.icon().pixmap(16, 16).toImage(),
    )

    assert row_toggle.icon().pixmap(16, 16).toImage() in eye_images
    assert all(filter_image != eye_image for eye_image in eye_images)
    assert "this list" in browser._show_excluded_toggle.toolTip()
    assert "playback" in row_toggle.toolTip()


def _actions_by_text(menu: QMenu) -> dict[str, QAction]:
    return {action.text(): action for action in menu.actions()}


def test_an_unavailable_row_shows_its_reason_instead_of_opening(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    item = VideoItem(video=Path("/missing/lot.mp4"))
    browser.set_browser_rows(
        (
            WorkspaceBrowserRow(
                row_id=item.id,
                label="lot.mp4",
                icon_kind="unavailable",
                item=item,
                unavailable_reason="File not found: /missing/lot.mp4",
            ),
        )
    )
    activated: list[object] = []
    browser.content_activated.connect(lambda content, index: activated.append(content))
    tree_item = _required_top_item(browser)

    with patch("ax_devil.modules.workspace.ui.content_browser.QMessageBox.warning") as warning:
        browser._tree.itemDoubleClicked.emit(tree_item, TREE_LABEL_COLUMN)

    assert activated == []
    [(_, _, message)] = [call.args for call in warning.call_args_list]
    assert message == "lot.mp4: File not found: /missing/lot.mp4"
    assert "File not found: /missing/lot.mp4" in tree_item.toolTip(TREE_LABEL_COLUMN)
    actions = _actions_by_text(browser._build_context_menu(tree_item))
    assert set(actions) == {"Rename", "Remove"}
    with qtbot.waitSignal(browser.item_remove_requested) as removed:
        actions["Remove"].trigger()
    assert removed.args == [item.id]


def test_rename_asks_for_a_name_and_requests_it(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    item = VideoItem(label="Gate", video=Path("/tmp/gate.mp4"))
    browser.set_browser_rows((WorkspaceBrowserRow(row_id="gate", label="Gate", icon_kind="video", item=item),))
    shown: list[str] = []

    def enter_name(dialog: RenameDialog) -> int:
        shown.append(dialog._name_edit.text())
        dialog._name_edit.setText("  North gate ")
        return RenameDialog.DialogCode.Accepted

    monkeypatch.setattr(RenameDialog, "exec", enter_name)
    with qtbot.waitSignal(browser.item_rename_requested) as renamed:
        _actions_by_text(browser._build_context_menu(_required_top_item(browser)))["Rename"].trigger()

    assert shown == ["Gate"]
    assert renamed.args == [item.id, "North gate"]


def test_an_unreadable_item_cannot_be_renamed(qtbot: QtBot) -> None:
    browser = ContentBrowserWidget()
    qtbot.addWidget(browser)
    item = UnreadableItem.from_raw({"kind": "radar", "id": "r1"}, "Unknown item kind: 'radar'.")
    browser.set_browser_rows(
        (
            WorkspaceBrowserRow(
                row_id=item.id, label="radar", icon_kind="unavailable", item=item, unavailable_reason=item.reason
            ),
        )
    )

    actions = _actions_by_text(browser._build_context_menu(_required_top_item(browser)))

    assert not actions["Rename"].isEnabled()
    assert actions["Remove"].isEnabled()
