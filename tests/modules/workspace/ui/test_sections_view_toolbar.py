"""A sections view's Filter and Sort choose which rows it lists, and its footer sits under the tree."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.workspace.ui.browser_rows import (
    BY_NAME,
    WORKSPACE_ORDER,
    BrowserSection,
    Facet,
    RowOrder,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.sections_view import SectionsView
from tests.helpers.section_tree import caption, captions, titles
from tests.helpers.sections_view import filter_entries, search_box, set_rows, toolbar_button, view_tree, view_with


def _video_row(name: str) -> WorkspaceBrowserRow:
    return WorkspaceBrowserRow(row_id=name, label=name, icon_kind="video")


def _choose_in_filter(view: SectionsView, entry: str) -> None:
    """Check or uncheck *entry* in the Filter, as the user does."""
    [button] = [button for button in view.findChildren(MenuButton) if button.text() == "Filter"]
    menu = button.menu()
    assert menu is not None
    [action] = [action for action in menu.actions() if action.text() == entry]
    action.trigger()


def test_the_filter_is_absent_without_a_choice_and_appears_with_one(qtbot: QtBot) -> None:
    view = view_with(qtbot, _camera("Q1656", "Box Camera"))
    assert toolbar_button(view, "Filter") is None

    set_rows(view, (_camera("Q1656", "Box Camera"), _camera("C1310", "Network Speaker")))
    assert toolbar_button(view, "Filter") is not None

    set_rows(view, (_camera("Q1656", "Box Camera"),))
    assert toolbar_button(view, "Filter") is None


def _choose_order(view: SectionsView, label: str) -> None:
    """Pick the order *label* in the Sort, as the user does."""
    sort = toolbar_button(view, "Sort")
    assert sort is not None
    menu = sort.menu()
    assert menu is not None
    [action] = [action for action in menu.actions() if action.text() == label]
    action.trigger()


def _ordered_view(qtbot: QtBot, *orders: RowOrder) -> SectionsView:
    view = view_with(
        qtbot,
        *(WorkspaceBrowserRow(row_id=name, label=name, icon_kind="video") for name in ("b.mp4", "C.mp4", "a.mp4")),
    )
    view.set_orders(orders)
    return view


def test_sort_is_absent_with_one_order_and_lists_rows_as_given(qtbot: QtBot) -> None:
    view = _ordered_view(qtbot, WORKSPACE_ORDER)

    assert toolbar_button(view, "Sort") is None
    assert titles(view.tree) == ["b.mp4", "C.mp4", "a.mp4"]


def test_choosing_an_order_lists_the_rows_again_and_the_order_survives_new_sections(qtbot: QtBot) -> None:
    view = _ordered_view(qtbot, WORKSPACE_ORDER, BY_NAME)

    _choose_order(view, "Name")
    assert titles(view.tree) == ["a.mp4", "b.mp4", "C.mp4"]

    set_rows(view, tuple(_video_row(name) for name in ("b.mp4", "C.mp4", "0.mp4", "a.mp4")))
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
    view_tree(view).expandAll()

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

    set_rows(view, (_video_row("a.mp4"),))

    assert not empty.isVisibleTo(view) and add.isVisibleTo(view) and panel.isVisibleTo(view)
    tree_bottom = view.tree.mapTo(view, view.tree.rect().bottomLeft()).y()
    assert tree_bottom <= add.mapTo(view, add.rect().topLeft()).y() < panel.mapTo(view, panel.rect().topLeft()).y()


def _camera(name: str, product_type: str) -> WorkspaceBrowserRow:
    return WorkspaceBrowserRow(
        row_id=name, label=name, icon_kind="live_video", facets=frozenset({Facet("Type", product_type)})
    )


def _filtered_view(qtbot: QtBot, *rows: WorkspaceBrowserRow) -> SectionsView:
    """Return a view of *rows* whose Filter hides Network Speaker."""
    view = view_with(qtbot, *rows)
    _choose_in_filter(view, "Network Speaker")
    return view


def test_a_search_matching_only_filtered_out_rows_keeps_the_section_listed(qtbot: QtBot) -> None:
    view = _filtered_view(qtbot, _camera("Q1656", "Box Camera"), _camera("C1310", "Network Speaker"))

    search_box(view).setText("c1310")

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

    search_box(view).setText("q1656")

    assert captions(view.tree) == ["Videos, 2"]

    search_box(view).setText("c1310")

    assert captions(view.tree) == ["Videos, 2, 1 filtered out"]


def test_a_section_counts_a_filtered_out_entry_with_the_lane_under_it_matching_a_search(qtbot: QtBot) -> None:
    lane = WorkspaceBrowserRow(row_id="lane", label="Overlay lane", icon_kind="overlay")
    speaker = replace(_camera("C1310", "Network Speaker"), children=(lane,))
    view = _filtered_view(qtbot, _camera("Q1656", "Box Camera"), speaker)

    search_box(view).setText("lane")

    assert captions(view.tree) == ["Videos, 2, 2 filtered out"]


def test_the_filter_offers_a_group_once_it_has_a_choice_and_hides_rows_by_it(qtbot: QtBot) -> None:
    view = view_with(qtbot, _camera("Q1656", "Box Camera"))
    assert filter_entries(view) == {}, "one type is no choice"

    set_rows(view, (_camera("Q1656", "Box Camera"), _camera("C1310", "Network Speaker")))
    assert filter_entries(view) == {"Box Camera": True, "Network Speaker": True}

    _choose_in_filter(view, "Network Speaker")
    assert titles(view.tree) == ["Q1656"]

    set_rows(view, (_camera("C1310", "Network Speaker"),))
    assert filter_entries(view) == {"Network Speaker": False}, "a hidden type stays offered, to show it again"
