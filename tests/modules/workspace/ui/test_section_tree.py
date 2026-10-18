"""The section tree shows browser sections with the shared source row: playlist rows, expansion and folding."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.ui.browser_rows import BrowserSection, WorkspaceBrowserRow, WorkspaceBrowserSection
from tests.helpers.section_tree import caption, click_caption, is_folded, texts, titles
from tests.helpers.section_tree_rows import item_at, playlist_row, row_widget, show_tree


def _with_open_entry(playlist: WorkspaceBrowserRow, index: int) -> WorkspaceBrowserRow:
    """Return *playlist* as the viewer shows it: open, with only the entry at *index* open."""
    return replace(
        playlist,
        is_open=True,
        children=tuple(replace(entry, is_open=position == index) for position, entry in enumerate(playlist.children)),
    )


def test_a_collapsed_playlist_gets_entry_widgets_only_once_it_is_expanded(qtbot: QtBot) -> None:
    tree = show_tree(qtbot, playlist_row(300))
    playlist = item_at(tree, 0)
    entries = [item_at(tree, 0, index) for index in range(playlist.childCount())]

    assert row_widget(tree, playlist).row.label == "Night shift"
    assert all(tree.itemWidget(entry, 0) is None for entry in entries)

    playlist.setExpanded(True)

    assert [row_widget(tree, entry).row.label for entry in entries[:2]] == ["run_000", "run_001"]
    assert all(tree.itemWidget(entry, 0) is not None for entry in entries)
    assert tree.itemWidget(item_at(tree, 0, 0, 0), 0) is None, "lanes wait for their own entry"


def test_a_playlist_shows_its_entries_once_it_resolves_and_its_lanes_stay_hidden_until_an_entry_opens(
    qtbot: QtBot,
) -> None:
    pending = WorkspaceBrowserRow(row_id="item", label="Night shift", icon_kind="playlist", idle=True)
    tree = show_tree(qtbot, pending)
    assert not item_at(tree, 0).isExpanded()

    tree.set_sections(_sections(playlist_row(3, expanded_at_first=True)))

    assert item_at(tree, 0).isExpanded()
    assert [row_widget(tree, item_at(tree, 0, index)).row.label for index in range(3)] == [
        "run_000",
        "run_001",
        "run_002",
    ]
    assert not any(item_at(tree, 0, index).isExpanded() for index in range(3))
    assert tree.itemWidget(item_at(tree, 0, 0, 0), 0) is None, "lanes wait for their own entry"

    tree.set_sections(_sections(_with_open_entry(playlist_row(3, expanded_at_first=True), 1)))

    assert item_at(tree, 0, 1).isExpanded()
    assert row_widget(tree, item_at(tree, 0, 1, 0)).row.label == "tracks"


def test_a_playlist_the_user_collapsed_stays_collapsed_across_updates(qtbot: QtBot) -> None:
    tree = show_tree(qtbot, playlist_row(3, expanded_at_first=True))
    item_at(tree, 0).setExpanded(False)

    tree.set_sections(_sections(playlist_row(4, expanded_at_first=True)))
    assert not item_at(tree, 0).isExpanded()

    tree.set_sections(
        _sections(playlist_row(4, "other", expanded_at_first=True), playlist_row(4, expanded_at_first=True))
    )
    assert not item_at(tree, 1).isExpanded()
    assert item_at(tree, 0).isExpanded(), "a playlist added later still starts expanded"


def test_search_finds_rows_whose_widgets_are_not_built_yet(qtbot: QtBot) -> None:
    tree = show_tree(qtbot, playlist_row(300))

    tree.set_filter(lambda _row: True, lambda row: row.label == "run_250")

    assert item_at(tree, 0).isExpanded()
    assert not item_at(tree, 0, 250).isHidden()
    assert item_at(tree, 0, 249).isHidden()
    assert row_widget(tree, item_at(tree, 0, 250)).row.label == "run_250"


def test_expanded_rows_stay_expanded_with_their_rows_across_updates(qtbot: QtBot) -> None:
    tree = show_tree(qtbot, playlist_row(3, "first"), playlist_row(3, "second"))
    item_at(tree, 1).setExpanded(True)

    reordered = (playlist_row(4, "second"), playlist_row(3, "first"))
    tree.set_sections((WorkspaceBrowserSection(BrowserSection.PLAYLISTS, reordered),))

    assert item_at(tree, 0).isExpanded() and not item_at(tree, 1).isExpanded()
    assert [row_widget(tree, item_at(tree, 0, index)).row.label for index in range(4)] == [
        "run_000",
        "run_001",
        "run_002",
        "run_003",
    ]


def test_moving_the_open_entry_collapses_the_previous_entry_and_expands_the_new_one(qtbot: QtBot) -> None:
    tree = show_tree(qtbot, _with_open_entry(playlist_row(3), 0))

    tree.set_sections(_sections(_with_open_entry(playlist_row(3), 1)))

    assert item_at(tree, 0).isExpanded()
    assert not item_at(tree, 0, 0).isExpanded()
    assert item_at(tree, 0, 1).isExpanded()


def test_the_list_keeps_its_scroll_position_when_the_rows_change(qtbot: QtBot) -> None:
    """Opening an entry far down the list refreshes the rows; the list must not jump back to the top."""
    playlists = tuple(playlist_row(1, f"playlist-{index}") for index in range(40))
    tree = show_tree(qtbot, *playlists)
    tree.resize(300, 300)
    scroll_bar = tree.verticalScrollBar()
    qtbot.waitUntil(lambda: scroll_bar.maximum() > 0)
    position = scroll_bar.maximum() // 2
    scroll_bar.setValue(position)

    tree.set_sections((WorkspaceBrowserSection(BrowserSection.PLAYLISTS, playlists),))
    QApplication.processEvents()  # Let the tree run its delayed layout, where a jump to the top would happen.

    assert scroll_bar.value() == position


def _sections(*rows: WorkspaceBrowserRow) -> tuple[WorkspaceBrowserSection, ...]:
    return (WorkspaceBrowserSection(BrowserSection.PLAYLISTS, rows),)


def test_clicking_a_caption_folds_its_section_and_the_fold_survives_an_update(qtbot: QtBot) -> None:
    tree = show_tree(qtbot, playlist_row(1))

    click_caption(tree, "Playlists")

    assert is_folded(tree, "Playlists")
    assert titles(tree) == []

    tree.set_sections(_sections(playlist_row(1)))

    assert is_folded(tree, "Playlists")

    click_caption(tree, "Playlists")

    assert titles(tree) == ["Night shift"]


def test_a_folded_section_unfolds_for_a_search_and_folds_again_after_it(qtbot: QtBot) -> None:
    tree = show_tree(qtbot, playlist_row(1))
    click_caption(tree, "Playlists")

    tree.set_filter(lambda _row: True, lambda row: row.label == "Night shift")

    assert not is_folded(tree, "Playlists")

    tree.set_sections(_sections(playlist_row(1)))
    tree.set_filter(lambda _row: True)

    assert is_folded(tree, "Playlists")


def test_a_folded_section_unfolds_for_an_open_row_and_folds_again_when_it_closes(qtbot: QtBot) -> None:
    tree = show_tree(qtbot, playlist_row(1))
    click_caption(tree, "Playlists")
    opened = replace(playlist_row(1), is_open=True)

    tree.set_sections(_sections(opened))
    tree.set_sections(_sections(opened))

    assert not is_folded(tree, "Playlists")

    tree.set_sections(_sections(playlist_row(1)))

    assert is_folded(tree, "Playlists")


def test_folding_a_section_keeps_it_folded_while_its_row_stays_open(qtbot: QtBot) -> None:
    opened = replace(playlist_row(1), is_open=True)
    tree = show_tree(qtbot, opened)

    click_caption(tree, "Playlists")
    tree.set_sections(_sections(opened))

    assert is_folded(tree, "Playlists")


def test_a_section_without_rows_says_so_and_does_not_fold(qtbot: QtBot) -> None:
    tree = show_tree(qtbot, kind=BrowserSection.NETWORK)

    click_caption(tree, "On the network")

    assert not is_folded(tree, "On the network")
    assert "No new cameras" in texts(tree)
    assert caption(tree, "On the network").text() == "On the network"
