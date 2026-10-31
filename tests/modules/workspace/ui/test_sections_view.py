"""A sections view lists its rows, searches them, and reports what the user does with them."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QMenu, QTreeWidget, QTreeWidgetItem, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.core import ConsiderationItemRef, FileVideoSourceSpec, SeekableVideoContent, VideoItem
from ax_devil.modules.workspace.core.item_info import WorkspaceItemInfo
from ax_devil.modules.workspace.ui.browser_rows import (
    BrowserOpenTarget,
    BrowserSection,
    OfferedAction,
    RowAction,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.item_info_dialog import WorkspaceItemInfoDialog
from ax_devil.modules.workspace.ui.section_tree import ROW_ROLE
from ax_devil.modules.workspace.ui.sections_view import SectionsView
from ax_devil.modules.workspace.ui.source_row import SourceRow
from tests.helpers.section_tree import titles
from tests.helpers.sections_view import new_view, search_box, set_rows, view_tree, view_with
from tests.helpers.widgets import required_child


def _make_video(name: str = "test.mp4") -> SeekableVideoContent:
    return SeekableVideoContent(display_name=name, source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")))


def _row(item: QTreeWidgetItem) -> WorkspaceBrowserRow:
    return cast(WorkspaceBrowserRow, item.data(0, ROW_ROLE))


def _row_widget(view: SectionsView, item: QTreeWidgetItem) -> SourceRow:
    widget = view_tree(view).itemWidget(item, 0)
    assert isinstance(widget, SourceRow)
    return widget


def _required_item_row(view: SectionsView, index: int = 0) -> QTreeWidgetItem:
    section = view_tree(view).topLevelItem(0)
    assert section is not None
    return required_child(section, index)


def test_renders_explicit_rows(qtbot: QtBot) -> None:
    view = view_with(
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

    top_item = _required_item_row(view)
    assert _row(top_item).label == "Suite"
    assert [_row(required_child(top_item, index)).label for index in range(top_item.childCount())] == [
        "Entry 1",
        "Entry 2",
    ]


def test_refresh_preserves_expansion_by_identity_after_reordering(qtbot: QtBot) -> None:
    child = WorkspaceBrowserRow(row_id="child", label="Entry", icon_kind="video", is_open=True)
    first = WorkspaceBrowserRow(row_id="first", label="Same name", icon_kind="playlist", children=(child,))
    second = WorkspaceBrowserRow(
        row_id="second", label="Same name", icon_kind="playlist", children=(replace(child, row_id="other-child"),)
    )
    view = view_with(qtbot, first, second)
    _required_item_row(view, 0).setExpanded(False)
    _required_item_row(view, 1).setExpanded(True)

    set_rows(view, (second, replace(first, label="Renamed")))

    assert _required_item_row(view, 0).isExpanded()
    assert not _required_item_row(view, 1).isExpanded()


def test_open_row_expands_its_parent(qtbot: QtBot) -> None:
    view = view_with(
        qtbot,
        WorkspaceBrowserRow(
            row_id="row-11",
            label="Suite",
            icon_kind="playlist",
            children=(WorkspaceBrowserRow(row_id="row-12", label="Entry 1", icon_kind="video", is_open=True),),
        ),
    )

    top_item = _required_item_row(view)
    assert top_item.isExpanded()
    child_item = required_child(top_item, 0)
    assert _row(child_item).is_open
    assert "Open in a viewer" in _row_widget(view, child_item).row.tooltip


def test_a_section_row_is_not_selectable_or_openable_and_is_not_searchable(qtbot: QtBot) -> None:
    view = view_with(qtbot, WorkspaceBrowserRow(row_id="clip", label="clip.mp4", icon_kind="video"))
    section = view_tree(view).topLevelItem(0)
    assert section is not None

    assert section.isExpanded()
    assert not section.flags() & Qt.ItemFlag.ItemIsSelectable
    assert section.toolTip(0) == ""
    activated = Mock()
    view.tree.row_action_requested.connect(activated)
    view_tree(view).itemDoubleClicked.emit(section, 0)
    activated.assert_not_called()
    assert _row(required_child(section, 0)).label == "clip.mp4"

    search_box(view).setText("videos")

    assert section.isHidden()
    assert required_child(section, 0).isHidden()

    search_box(view).setText("clip")

    assert not section.isHidden()
    assert not required_child(section, 0).isHidden()


def test_tree_items_expose_their_detail_as_accessible_text(qtbot: QtBot) -> None:
    view = new_view()
    qtbot.addWidget(view)
    view.set_sections(
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
    section = view_tree(view).topLevelItem(0)
    assert section is not None

    def accessible_text(item: QTreeWidgetItem) -> object:
        return item.data(0, Qt.ItemDataRole.AccessibleTextRole)

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
    view = view_with(qtbot, *rows)
    search_box(view).setText("Match")
    _required_item_row(view).setExpanded(False)

    set_rows(view, rows)

    assert _required_item_row(view).isExpanded()


def test_hidden_rows_are_revealed_without_unhiding_and_stay_filtered_in_search(qtbot: QtBot) -> None:
    hidden = _hidden("Warehouse door")
    view = view_with(qtbot, hidden)
    assert titles(view.tree) == []
    [toggle] = [button for button in view.findChildren(QAbstractButton) if button.toolTip() == "Show hidden items"]
    assert toggle.isVisibleTo(view)

    search_box(view).setText("warehouse")
    assert titles(view.tree) == []

    toggle.click()
    assert titles(view.tree) == ["Warehouse door"]
    assert _row(_required_item_row(view)).is_dimmed

    set_rows(view, (hidden,))
    assert titles(view.tree) == ["Warehouse door"]
    toggle.click()
    assert titles(view.tree) == []

    set_rows(view, (replace(hidden, is_considered=True),))

    assert titles(view.tree) == ["Warehouse door"]
    assert not _row(_required_item_row(view)).is_dimmed


def test_search_filters_workspace_rows_by_name_and_location(qtbot: QtBot) -> None:
    view = view_with(
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
    search = search_box(view)
    location_row = _required_item_row(view, 1)
    assert _row(location_row).label == "cam.mp4"
    assert "/data/site_a/cam.mp4" in _row_widget(view, location_row).row.tooltip

    search.setText("parking")
    assert not _required_item_row(view, 0).isHidden()
    assert location_row.isHidden()

    search.setText("site_a")
    assert _required_item_row(view, 0).isHidden()
    assert not location_row.isHidden()

    search.clear()
    assert not _required_item_row(view, 0).isHidden()
    assert not location_row.isHidden()


def test_search_keeps_matching_playlist_children_reachable(qtbot: QtBot) -> None:
    view = view_with(
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

    search_box(view).setText("warehouse")

    top_item = _required_item_row(view)
    assert not top_item.isHidden()
    assert required_child(top_item, 0).isHidden()
    assert not required_child(top_item, 1).isHidden()
    assert top_item.isExpanded()


def _double_click(
    widget: QWidget, modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier, position: QPoint | None = None
) -> None:
    """Double-click *widget*, a row or a control on one, as the user does: a click, then a double click."""
    position = position or QPoint(widget.width() // 3, widget.height() // 2)
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, modifier, pos=position)
    QTest.mouseDClick(widget, Qt.MouseButton.LeftButton, modifier, pos=position)


def test_double_click_asks_to_open_the_row_and_expands_it(qtbot: QtBot) -> None:
    """Double-clicking a collapsed row asks to open it and leaves its children visible instead of toggling them."""
    video = _make_video()
    view = view_with(
        qtbot,
        WorkspaceBrowserRow(
            row_id="row-13",
            label="Video",
            icon_kind="video",
            activation_target=BrowserOpenTarget(video, 0),
            children=(WorkspaceBrowserRow(row_id="row-14", label="Overlay", icon_kind="overlay"),),
        ),
    )
    view.show()
    top_item = _required_item_row(view)
    top_item.setExpanded(False)

    with qtbot.waitSignal(view.tree.row_action_requested, timeout=1000) as blocker:
        _double_click(_row_widget(view, top_item))

    assert blocker.args == [_row(top_item), RowAction.OPEN]
    assert top_item.isExpanded()


def test_context_menus_and_actions_are_disposed_after_closing(qtbot: QtBot) -> None:
    view = view_with(
        qtbot,
        WorkspaceBrowserRow(
            row_id="video",
            label="Video",
            icon_kind="video",
            item=VideoItem(video=Path("/tmp/video.mp4")),
            actions=(OfferedAction(RowAction.EDIT), OfferedAction(RowAction.REMOVE)),
        ),
    )
    view.show()
    tree = view_tree(view)
    position = tree.visualItemRect(_required_item_row(view)).center()
    destroyed = Mock()

    def close_menu() -> None:
        for menu in view.tree.findChildren(QMenu):
            menu.destroyed.connect(destroyed)
            for action in menu.actions():
                assert action.parent() is menu
                action.destroyed.connect(destroyed)
            menu.close()

    for _ in range(3):
        QTimer.singleShot(0, close_menu)
        tree.customContextMenuRequested.emit(position)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert destroyed.call_count == 9  # Each time: the menu, Edit, and Remove.
    assert view.tree.findChildren(QMenu) == []


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


def test_an_unavailable_row_shows_its_reason(qtbot: QtBot) -> None:
    item = VideoItem(video=Path("/missing/lot.mp4"))
    view = view_with(
        qtbot,
        WorkspaceBrowserRow(
            row_id=item.id,
            label="lot.mp4",
            icon_kind="unavailable",
            item=item,
            unavailable_reason="File not found: /missing/lot.mp4",
        ),
    )

    assert "File not found: /missing/lot.mp4" in _row_widget(view, _required_item_row(view)).row.tooltip


def _hidden(label: str) -> WorkspaceBrowserRow:
    """Return a playlist entry row the user hid from playback."""
    ref = ConsiderationItemRef("item-id", "Suite/a.mp4")
    return WorkspaceBrowserRow(row_id=label, label=label, icon_kind="video", consideration_ref=ref, is_considered=False)
