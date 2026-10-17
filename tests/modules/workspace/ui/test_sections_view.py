"""A sections view lists its rows, searches and filters them, and reports what the user does with them."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMenu, QToolButton, QTreeWidget, QTreeWidgetItem, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.workspace.core import (
    ConsiderationItemRef,
    FileVideoSourceSpec,
    SeekableVideoContent,
    UnreadableItem,
    VideoItem,
)
from ax_devil.modules.workspace.core.content import OnScreenWorkspaceItem
from ax_devil.modules.workspace.core.item_info import WorkspaceItemInfo
from ax_devil.modules.workspace.ui.browser_rows import (
    BY_NAME,
    WORKSPACE_ORDER,
    BrowserSection,
    Facet,
    RowOrder,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.item_info_dialog import WorkspaceItemInfoDialog
from ax_devil.modules.workspace.ui.rename_dialog import RenameDialog
from ax_devil.modules.workspace.ui.section_tree import ROW_ROLE
from ax_devil.modules.workspace.ui.sections_view import SectionsView
from ax_devil.modules.workspace.ui.source_row import SourceRow
from tests.helpers.section_tree import caption, captions, titles
from tests.helpers.widgets import required_child


def _video_row(name: str) -> WorkspaceBrowserRow:
    return WorkspaceBrowserRow(row_id=name, label=name, icon_kind="video")


def _make_video(name: str = "test.mp4") -> SeekableVideoContent:
    return SeekableVideoContent(display_name=name, source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")))


def _new_view() -> SectionsView:
    return SectionsView("test", "Test", Icon.WORKSPACE)


def _view_with(qtbot: QtBot, *rows: WorkspaceBrowserRow) -> SectionsView:
    view = _new_view()
    qtbot.addWidget(view)
    _set_rows(view, rows)
    return view


def _set_rows(view: SectionsView, rows: tuple[WorkspaceBrowserRow, ...]) -> None:
    view.set_sections((WorkspaceBrowserSection(BrowserSection.VIDEOS, rows),))


def _tree(view: SectionsView) -> QTreeWidget:
    tree: QTreeWidget | None = view.findChild(QTreeWidget)
    assert tree is not None
    return tree


def _search(view: SectionsView) -> QLineEdit:
    search: QLineEdit | None = view.findChild(QLineEdit)
    assert search is not None
    return search


def _menu_button(view: SectionsView, text: str) -> MenuButton | None:
    """Return the toolbar button *text* if the view shows it, else None."""
    [button] = [button for button in view.findChildren(MenuButton) if button.text() == text]
    return button if button.isVisibleTo(view) else None


def _filter_entries(view: SectionsView) -> dict[str, bool]:
    """Return what the Filter offers, each entry with whether its rows are shown; empty while it is absent."""
    [button] = [button for button in view.findChildren(MenuButton) if button.text() == "Filter"]
    menu = button.menu()
    assert menu is not None
    if not button.isVisibleTo(view):
        return {}
    return {action.text(): action.isChecked() for action in menu.actions() if not action.isSeparator()}


def _choose_in_filter(view: SectionsView, entry: str) -> None:
    """Check or uncheck *entry* in the Filter, as the user does."""
    [button] = [button for button in view.findChildren(MenuButton) if button.text() == "Filter"]
    menu = button.menu()
    assert menu is not None
    [action] = [action for action in menu.actions() if action.text() == entry]
    action.trigger()


def _row(item: QTreeWidgetItem) -> WorkspaceBrowserRow:
    return cast(WorkspaceBrowserRow, item.data(0, ROW_ROLE))


def _row_widget(view: SectionsView, item: QTreeWidgetItem) -> SourceRow:
    widget = _tree(view).itemWidget(item, 0)
    assert isinstance(widget, SourceRow)
    return widget


def _required_item_row(view: SectionsView, index: int = 0) -> QTreeWidgetItem:
    section = _tree(view).topLevelItem(0)
    assert section is not None
    return required_child(section, index)


def _context_menu(
    view: SectionsView,
    item: QTreeWidgetItem,
    choose: str | None = None,
    after_choosing: Callable[[], None] | None = None,
) -> dict[str, bool]:
    """Right-click *item*, return its menu entries with their enabled state, and optionally pick one.

    *after_choosing* runs once the chosen action is underway, for closing a dialog it opens.
    """
    view.show()
    tree = _tree(view)
    entries: dict[str, bool] = {}

    def inspect_menu() -> None:
        menu = view.tree.findChildren(QMenu)[-1]
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
    view = _view_with(
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
    view = _view_with(qtbot, first, second)
    _required_item_row(view, 0).setExpanded(False)
    _required_item_row(view, 1).setExpanded(True)

    _set_rows(view, (second, replace(first, label="Renamed")))

    assert _required_item_row(view, 0).isExpanded()
    assert not _required_item_row(view, 1).isExpanded()


def test_open_row_expands_its_parent(qtbot: QtBot) -> None:
    view = _view_with(
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
    assert "Open in a viewer" in _row_widget(view, child_item).toolTip()


def test_a_section_row_is_not_selectable_or_openable_and_is_not_searchable(qtbot: QtBot) -> None:
    view = _view_with(qtbot, WorkspaceBrowserRow(row_id="clip", label="clip.mp4", icon_kind="video"))
    section = _tree(view).topLevelItem(0)
    assert section is not None

    assert section.isExpanded()
    assert not section.flags() & Qt.ItemFlag.ItemIsSelectable
    assert section.toolTip(0) == ""
    activated = Mock()
    view.tree.row_activated.connect(activated)
    _tree(view).itemDoubleClicked.emit(section, 0)
    activated.assert_not_called()
    assert _row(required_child(section, 0)).label == "clip.mp4"

    _search(view).setText("videos")

    assert section.isHidden()
    assert required_child(section, 0).isHidden()

    _search(view).setText("clip")

    assert not section.isHidden()
    assert not required_child(section, 0).isHidden()


def test_tree_items_expose_their_detail_as_accessible_text(qtbot: QtBot) -> None:
    view = _new_view()
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
    section = _tree(view).topLevelItem(0)
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
    view = _view_with(qtbot, *rows)
    _search(view).setText("Match")
    _required_item_row(view).setExpanded(False)

    _set_rows(view, rows)

    assert _required_item_row(view).isExpanded()


def test_a_hidden_row_stays_listed_and_dimmed_also_in_a_search(qtbot: QtBot) -> None:
    hidden = _hidden("Warehouse door")
    view = _view_with(qtbot, hidden)
    assert titles(view.tree) == ["Warehouse door"]
    assert _row(_required_item_row(view)).is_dimmed
    assert _filter_entries(view) == {}, "hiding a row is not a Filter choice"

    _search(view).setText("warehouse")

    assert titles(view.tree) == ["Warehouse door"]

    _set_rows(view, (replace(hidden, is_considered=True),))

    assert not _row(_required_item_row(view)).is_dimmed


def test_search_filters_workspace_rows_by_name_and_location(qtbot: QtBot) -> None:
    view = _view_with(
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
    search = _search(view)
    location_row = _required_item_row(view, 1)
    assert _row(location_row).label == "cam.mp4"
    assert "/data/site_a/cam.mp4" in _row_widget(view, location_row).toolTip()

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
    view = _view_with(
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

    _search(view).setText("warehouse")

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
    view = _view_with(
        qtbot,
        WorkspaceBrowserRow(
            row_id="row-13",
            label="Video",
            icon_kind="video",
            activation_target=(video, 0),
            children=(WorkspaceBrowserRow(row_id="row-14", label="Overlay", icon_kind="overlay"),),
        ),
    )
    view.show()
    top_item = _required_item_row(view)
    top_item.setExpanded(False)

    with qtbot.waitSignal(view.tree.row_activated, timeout=1000) as blocker:
        _double_click(_row_widget(view, top_item))

    assert blocker.args == [_row(top_item), False]
    assert top_item.isExpanded()


def test_context_menu_shows_information_on_request_and_removes_only_top_level_items(qtbot: QtBot) -> None:
    item = VideoItem(video=Path("/tmp/video.mp4"))
    info = WorkspaceItemInfo(title="Video", fields=(("type", "video"),))
    information_factory = Mock(return_value=info)
    view = _view_with(
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
    top_item = _required_item_row(view)
    top_item.setExpanded(True)

    assert list(_context_menu(view, top_item)) == ["Information", "Rename", "Remove"]
    assert list(_context_menu(view, required_child(top_item, 0))) == ["Information"]
    information_factory.assert_not_called()

    shown: list[str] = []

    def close_information() -> None:
        dialog = next(
            widget for widget in QApplication.topLevelWidgets() if isinstance(widget, WorkspaceItemInfoDialog)
        )
        shown.append(dialog.windowTitle())
        dialog.reject()

    _context_menu(view, top_item, choose="Information", after_choosing=close_information)
    information_factory.assert_called_once_with()
    assert len(shown) == 1

    with qtbot.waitSignal(view.tree.item_remove_requested) as removed:
        _context_menu(view, top_item, choose="Remove")
    assert removed.args == [item.id]


@pytest.mark.parametrize("is_open", [True, False])
def test_export_action_requests_the_open_item(qtbot: QtBot, is_open: bool) -> None:
    target = OnScreenWorkspaceItem(kind="video", content_id="video")
    view = _view_with(
        qtbot,
        WorkspaceBrowserRow(row_id="video", label="Video", icon_kind="video", export_target=target, is_open=is_open),
    )
    exported = Mock()
    view.tree.export_requested.connect(exported)

    [(label, enabled)] = _context_menu(view, _required_item_row(view)).items()
    assert enabled is is_open
    if is_open:
        _context_menu(view, _required_item_row(view), choose=label)
        exported.assert_called_once_with(target)


def test_context_menus_and_actions_are_disposed_after_closing(qtbot: QtBot) -> None:
    view = _view_with(
        qtbot,
        WorkspaceBrowserRow(
            row_id="video", label="Video", icon_kind="video", item=VideoItem(video=Path("/tmp/video.mp4"))
        ),
    )
    view.show()
    tree = _tree(view)
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

    assert destroyed.call_count == 9  # Each time: the menu, Rename, and Remove.
    assert view.tree.findChildren(QMenu) == []


def test_open_actions_open_in_preview_or_to_the_side(qtbot: QtBot) -> None:
    video = _make_video()
    view = _view_with(
        qtbot, WorkspaceBrowserRow(row_id="video", label="Video", icon_kind="video", activation_target=(video, 0))
    )
    item = _required_item_row(view)

    with qtbot.waitSignal(view.tree.content_open_to_side_requested) as side:
        _context_menu(view, item, choose="Open to the Side")
    with qtbot.waitSignal(view.tree.content_activated) as preview:
        _context_menu(view, item, choose="Open")

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


def test_an_unavailable_row_shows_its_reason_and_can_only_be_renamed_or_removed(qtbot: QtBot) -> None:
    item = VideoItem(video=Path("/missing/lot.mp4"))
    view = _view_with(
        qtbot,
        WorkspaceBrowserRow(
            row_id=item.id,
            label="lot.mp4",
            icon_kind="unavailable",
            item=item,
            unavailable_reason="File not found: /missing/lot.mp4",
        ),
    )
    tree_item = _required_item_row(view)

    assert "File not found: /missing/lot.mp4" in _row_widget(view, tree_item).toolTip()
    assert list(_context_menu(view, tree_item)) == ["Rename", "Remove"]
    with qtbot.waitSignal(view.tree.item_remove_requested) as removed:
        _context_menu(view, tree_item, choose="Remove")
    assert removed.args == [item.id]


def test_rename_asks_for_a_name_and_requests_it(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    item = VideoItem(label="Gate", video=Path("/tmp/gate.mp4"))
    view = _view_with(qtbot, WorkspaceBrowserRow(row_id="gate", label="Gate", icon_kind="video", item=item))
    shown: list[str] = []

    def enter_name(dialog: RenameDialog) -> int:
        shown.append(dialog._name_edit.text())
        dialog._name_edit.setText("  North gate ")
        return RenameDialog.DialogCode.Accepted

    monkeypatch.setattr(RenameDialog, "exec", enter_name)
    with qtbot.waitSignal(view.tree.item_rename_requested) as renamed:
        _context_menu(view, _required_item_row(view), choose="Rename")

    assert shown == ["Gate"]
    assert renamed.args == [item.id, "North gate"]


def test_renaming_an_unlabeled_row_seeds_the_shown_name_and_keeping_it_clears_the_label(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = VideoItem(video=Path("/tmp/gate.mp4"))
    shown_label = "Lot / gate.mp4"
    view = _view_with(qtbot, WorkspaceBrowserRow(row_id="gate", label=shown_label, icon_kind="video", item=item))
    entered: list[str] = []
    shown: list[str] = []

    def enter_name(dialog: RenameDialog) -> int:
        shown.append(dialog._name_edit.text())
        dialog._name_edit.setText(entered.pop(0))
        return RenameDialog.DialogCode.Accepted

    monkeypatch.setattr(RenameDialog, "exec", enter_name)
    for name, expected in ((shown_label, ""), ("North gate", "North gate")):
        entered.append(name)
        with qtbot.waitSignal(view.tree.item_rename_requested) as renamed:
            _context_menu(view, _required_item_row(view), choose="Rename")
        assert renamed.args == [item.id, expected]

    assert shown == [shown_label, shown_label]


def test_an_unreadable_item_cannot_be_renamed(qtbot: QtBot) -> None:
    item = UnreadableItem.from_raw({"kind": "radar", "id": "r1"}, "Unknown item kind: 'radar'.")
    view = _view_with(
        qtbot,
        WorkspaceBrowserRow(
            row_id=item.id, label="radar", icon_kind="unavailable", item=item, unavailable_reason=item.reason
        ),
    )

    assert _context_menu(view, _required_item_row(view)) == {"Rename": False, "Remove": True}


def _hidden(label: str) -> WorkspaceBrowserRow:
    """Return a playlist entry row the user hid from playback."""
    ref = ConsiderationItemRef.playlist_entry("playlist-id", 0)
    return WorkspaceBrowserRow(row_id=label, label=label, icon_kind="video", consideration_ref=ref, is_considered=False)


def test_the_filter_is_absent_without_a_choice_and_appears_with_one(qtbot: QtBot) -> None:
    view = _view_with(qtbot, _camera("Q1656", "Box Camera"))
    assert _menu_button(view, "Filter") is None

    _set_rows(view, (_camera("Q1656", "Box Camera"), _camera("C1310", "Network Speaker")))
    assert _menu_button(view, "Filter") is not None

    _set_rows(view, (_camera("Q1656", "Box Camera"),))
    assert _menu_button(view, "Filter") is None


def _choose_order(view: SectionsView, label: str) -> None:
    """Pick the order *label* in the Sort, as the user does."""
    sort = _menu_button(view, "Sort")
    assert sort is not None
    menu = sort.menu()
    assert menu is not None
    [action] = [action for action in menu.actions() if action.text() == label]
    action.trigger()


def _ordered_view(qtbot: QtBot, *orders: RowOrder) -> SectionsView:
    view = _view_with(
        qtbot,
        *(WorkspaceBrowserRow(row_id=name, label=name, icon_kind="video") for name in ("b.mp4", "C.mp4", "a.mp4")),
    )
    view.set_orders(orders)
    return view


def test_sort_is_absent_with_one_order_and_lists_rows_as_given(qtbot: QtBot) -> None:
    view = _ordered_view(qtbot, WORKSPACE_ORDER)

    assert _menu_button(view, "Sort") is None
    assert titles(view.tree) == ["b.mp4", "C.mp4", "a.mp4"]


def test_choosing_an_order_lists_the_rows_again_and_the_order_survives_new_sections(qtbot: QtBot) -> None:
    view = _ordered_view(qtbot, WORKSPACE_ORDER, BY_NAME)

    _choose_order(view, "Name")
    assert titles(view.tree) == ["a.mp4", "b.mp4", "C.mp4"]

    _set_rows(view, tuple(_video_row(name) for name in ("b.mp4", "C.mp4", "0.mp4", "a.mp4")))
    assert titles(view.tree) == ["0.mp4", "a.mp4", "b.mp4", "C.mp4"]


def test_an_order_sorts_the_rows_of_each_section_but_not_the_rows_under_them(qtbot: QtBot) -> None:
    view = _ordered_view(qtbot, WORKSPACE_ORDER, BY_NAME)
    entries = (_video_row("z.mp4"), _video_row("y.mp4"))
    view.set_sections(
        (
            WorkspaceBrowserSection(
                BrowserSection.PLAYLISTS,
                (
                    replace(_video_row("Night"), icon_kind="playlist", children=entries),
                    replace(_video_row("Day"), icon_kind="playlist"),
                ),
            ),
        )
    )
    _tree(view).expandAll()

    _choose_order(view, "Name")

    assert titles(view.tree) == ["Day", "Night", "z.mp4", "y.mp4"]


def test_the_footer_sits_under_the_tree_and_goes_with_the_toolbar_while_the_empty_page_shows(qtbot: QtBot) -> None:
    empty = QLabel("Nothing yet")
    view = SectionsView("test", "Test", Icon.WORKSPACE, empty_page=empty)
    qtbot.addWidget(view)
    add = view.add_footer_button("Add thing", Icon.ADD)
    panel = QLabel("Panel")
    view.set_footer_panel(panel)
    view.resize(300, 500)
    view.show()
    assert empty.isVisibleTo(view) and not add.isVisibleTo(view) and not panel.isVisibleTo(view)

    _set_rows(view, (_video_row("a.mp4"),))

    assert not empty.isVisibleTo(view) and add.isVisibleTo(view) and panel.isVisibleTo(view)
    tree_bottom = view.tree.mapTo(view, view.tree.rect().bottomLeft()).y()
    assert tree_bottom <= add.mapTo(view, add.rect().topLeft()).y() < panel.mapTo(view, panel.rect().topLeft()).y()


def _camera(name: str, product_type: str) -> WorkspaceBrowserRow:
    return WorkspaceBrowserRow(
        row_id=name, label=name, icon_kind="live_video", facets=frozenset({Facet("Type", product_type)})
    )


def _filtered_view(qtbot: QtBot, *rows: WorkspaceBrowserRow) -> SectionsView:
    """Return a view of *rows* whose Filter hides Network Speaker."""
    view = _view_with(qtbot, *rows)
    _choose_in_filter(view, "Network Speaker")
    return view


def test_a_search_matching_only_filtered_out_rows_keeps_the_section_listed(qtbot: QtBot) -> None:
    view = _filtered_view(qtbot, _camera("Q1656", "Box Camera"), _camera("C1310", "Network Speaker"))

    _search(view).setText("c1310")

    assert captions(view.tree) == ["Videos, 2, 1 filtered out"]
    assert titles(view.tree) == []


def test_a_section_counts_the_rows_the_filter_hides(qtbot: QtBot) -> None:
    view = _filtered_view(qtbot, _camera("Q1656", "Box Camera"), _camera("C1310", "Network Speaker"))

    assert caption(view.tree, "Videos").text() == "Videos  2  · 1 filtered out"
    assert captions(view.tree) == ["Videos, 2, 1 filtered out"]

    _choose_in_filter(view, "Network Speaker")

    assert caption(view.tree, "Videos").text() == "Videos  2"
    assert captions(view.tree) == ["Videos, 2"]


def test_a_section_counts_only_filtered_out_rows_the_search_would_otherwise_show(qtbot: QtBot) -> None:
    view = _filtered_view(qtbot, _camera("Q1656", "Box Camera"), _camera("C1310", "Network Speaker"))

    _search(view).setText("q1656")

    assert captions(view.tree) == ["Videos, 2"]

    _search(view).setText("c1310")

    assert captions(view.tree) == ["Videos, 2, 1 filtered out"]


def test_a_section_counts_a_filtered_out_entry_with_the_lane_under_it_matching_a_search(qtbot: QtBot) -> None:
    lane = WorkspaceBrowserRow(row_id="lane", label="Overlay lane", icon_kind="overlay")
    speaker = replace(_camera("C1310", "Network Speaker"), children=(lane,))
    view = _filtered_view(qtbot, _camera("Q1656", "Box Camera"), speaker)

    _search(view).setText("lane")

    assert captions(view.tree) == ["Videos, 2, 2 filtered out"]


def test_the_filter_offers_a_group_once_it_has_a_choice_and_hides_rows_by_it(qtbot: QtBot) -> None:
    view = _view_with(qtbot, _camera("Q1656", "Box Camera"))
    assert _filter_entries(view) == {}, "one type is no choice"

    _set_rows(view, (_camera("Q1656", "Box Camera"), _camera("C1310", "Network Speaker")))
    assert _filter_entries(view) == {"Box Camera": True, "Network Speaker": True}

    _choose_in_filter(view, "Network Speaker")
    assert titles(view.tree) == ["Q1656"]

    _set_rows(view, (_camera("C1310", "Network Speaker"),))
    assert _filter_entries(view) == {"Network Speaker": False}, "a hidden type stays offered, to show it again"
