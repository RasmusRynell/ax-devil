"""The section tree shows browser sections with the shared source row, and reports what the user does with rows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QTreeWidgetItem, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import apply_text_size
from ax_devil.modules.workspace.ui.browser_rows import (
    BrowserSection,
    OfferedAction,
    RowAction,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.section_tree import SectionTree, build_row_menu
from ax_devil.modules.workspace.ui.source_row import SourceRow
from tests.helpers.section_tree import (
    caption,
    click_caption,
    is_folded,
    press,
    row,
    row_texts,
    shown_rows,
    texts,
    titles,
)


def _tree(qtbot: QtBot, *rows: WorkspaceBrowserRow, kind: BrowserSection = BrowserSection.PLAYLISTS) -> SectionTree:
    tree = SectionTree()
    qtbot.addWidget(tree)
    tree.resize(360, 600)
    tree.set_sections((WorkspaceBrowserSection(kind, rows),))
    tree.show()
    qtbot.waitExposed(tree)
    return tree


def _item(tree: SectionTree, *path: int) -> QTreeWidgetItem:
    """Return the row item at *path* of child indexes below the first section."""
    item = tree.topLevelItem(0)
    for index in path:
        assert item is not None
        item = item.child(index)
    assert item is not None
    return item


def _widget(tree: SectionTree, item: QTreeWidgetItem) -> SourceRow:
    widget = tree.itemWidget(item, 0)
    assert isinstance(widget, SourceRow)
    return widget


def _playlist(entries: int, row_id: str = "playlist", *, expanded_at_first: bool = False) -> WorkspaceBrowserRow:
    lane = WorkspaceBrowserRow(row_id=f"{row_id}/lane", label="tracks", icon_kind="overlay")
    return WorkspaceBrowserRow(
        row_id=row_id,
        label="Night shift",
        icon_kind="playlist",
        actions=(OfferedAction(RowAction.RESCAN),),
        expanded_at_first=expanded_at_first,
        children=tuple(
            WorkspaceBrowserRow(
                row_id=f"{row_id}/entry/{index}",
                label=f"run_{index:03d}",
                icon_kind="video",
                children=(replace(lane, row_id=f"{row_id}/entry/{index}/lane"),),
            )
            for index in range(entries)
        ),
    )


def _with_open_entry(playlist: WorkspaceBrowserRow, index: int) -> WorkspaceBrowserRow:
    """Return *playlist* as the viewer shows it: open, with only the entry at *index* open."""
    return replace(
        playlist,
        is_open=True,
        children=tuple(replace(entry, is_open=position == index) for position, entry in enumerate(playlist.children)),
    )


def test_a_collapsed_playlist_gets_entry_widgets_only_once_it_is_expanded(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _playlist(300))
    playlist = _item(tree, 0)
    entries = [_item(tree, 0, index) for index in range(playlist.childCount())]

    assert _widget(tree, playlist).row.label == "Night shift"
    assert all(tree.itemWidget(entry, 0) is None for entry in entries)

    playlist.setExpanded(True)

    assert [_widget(tree, entry).row.label for entry in entries[:2]] == ["run_000", "run_001"]
    assert all(tree.itemWidget(entry, 0) is not None for entry in entries)
    assert tree.itemWidget(_item(tree, 0, 0, 0), 0) is None, "lanes wait for their own entry"


def test_a_playlist_shows_its_entries_once_it_resolves_and_its_lanes_stay_hidden_until_an_entry_opens(
    qtbot: QtBot,
) -> None:
    pending = WorkspaceBrowserRow(row_id="item", label="Night shift", icon_kind="playlist", idle=True)
    tree = _tree(qtbot, pending)
    assert not _item(tree, 0).isExpanded()

    tree.set_sections(_sections(_playlist(3, expanded_at_first=True)))

    assert _item(tree, 0).isExpanded()
    assert [_widget(tree, _item(tree, 0, index)).row.label for index in range(3)] == ["run_000", "run_001", "run_002"]
    assert not any(_item(tree, 0, index).isExpanded() for index in range(3))
    assert tree.itemWidget(_item(tree, 0, 0, 0), 0) is None, "lanes wait for their own entry"

    tree.set_sections(_sections(_with_open_entry(_playlist(3, expanded_at_first=True), 1)))

    assert _item(tree, 0, 1).isExpanded()
    assert _widget(tree, _item(tree, 0, 1, 0)).row.label == "tracks"


def test_a_playlist_the_user_collapsed_stays_collapsed_across_updates(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _playlist(3, expanded_at_first=True))
    _item(tree, 0).setExpanded(False)

    tree.set_sections(_sections(_playlist(4, expanded_at_first=True)))
    assert not _item(tree, 0).isExpanded()

    tree.set_sections(_sections(_playlist(4, "other", expanded_at_first=True), _playlist(4, expanded_at_first=True)))
    assert not _item(tree, 1).isExpanded()
    assert _item(tree, 0).isExpanded(), "a playlist added later still starts expanded"


def test_search_finds_rows_whose_widgets_are_not_built_yet(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _playlist(300))

    tree.set_filter(lambda _row: True, lambda row: row.label == "run_250")

    assert _item(tree, 0).isExpanded()
    assert not _item(tree, 0, 250).isHidden()
    assert _item(tree, 0, 249).isHidden()
    assert _widget(tree, _item(tree, 0, 250)).row.label == "run_250"


def test_expanded_rows_stay_expanded_with_their_rows_across_updates(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _playlist(3, "first"), _playlist(3, "second"))
    _item(tree, 1).setExpanded(True)

    reordered = (_playlist(4, "second"), _playlist(3, "first"))
    tree.set_sections((WorkspaceBrowserSection(BrowserSection.PLAYLISTS, reordered),))

    assert _item(tree, 0).isExpanded() and not _item(tree, 1).isExpanded()
    assert [_widget(tree, _item(tree, 0, index)).row.label for index in range(4)] == [
        "run_000",
        "run_001",
        "run_002",
        "run_003",
    ]


def test_moving_the_open_entry_collapses_the_previous_entry_and_expands_the_new_one(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _with_open_entry(_playlist(3), 0))

    tree.set_sections(_sections(_with_open_entry(_playlist(3), 1)))

    assert _item(tree, 0).isExpanded()
    assert not _item(tree, 0, 0).isExpanded()
    assert _item(tree, 0, 1).isExpanded()


def test_the_list_keeps_its_scroll_position_when_the_rows_change(qtbot: QtBot) -> None:
    """Opening an entry far down the list refreshes the rows; the list must not jump back to the top."""
    playlists = tuple(_playlist(1, f"playlist-{index}") for index in range(40))
    tree = _tree(qtbot, *playlists)
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
    tree = _tree(qtbot, _playlist(1))

    click_caption(tree, "Playlists")

    assert is_folded(tree, "Playlists")
    assert titles(tree) == []

    tree.set_sections(_sections(_playlist(1)))

    assert is_folded(tree, "Playlists")

    click_caption(tree, "Playlists")

    assert titles(tree) == ["Night shift"]


def test_a_folded_section_unfolds_for_a_search_and_folds_again_after_it(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _playlist(1))
    click_caption(tree, "Playlists")

    tree.set_filter(lambda _row: True, lambda row: row.label == "Night shift")

    assert not is_folded(tree, "Playlists")

    tree.set_sections(_sections(_playlist(1)))
    tree.set_filter(lambda _row: True)

    assert is_folded(tree, "Playlists")


def test_a_folded_section_unfolds_for_an_open_row_and_folds_again_when_it_closes(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _playlist(1))
    click_caption(tree, "Playlists")
    opened = replace(_playlist(1), is_open=True)

    tree.set_sections(_sections(opened))
    tree.set_sections(_sections(opened))

    assert not is_folded(tree, "Playlists")

    tree.set_sections(_sections(_playlist(1)))

    assert is_folded(tree, "Playlists")


def test_folding_a_section_keeps_it_folded_while_its_row_stays_open(qtbot: QtBot) -> None:
    opened = replace(_playlist(1), is_open=True)
    tree = _tree(qtbot, opened)

    click_caption(tree, "Playlists")
    tree.set_sections(_sections(opened))

    assert is_folded(tree, "Playlists")


def test_a_section_without_rows_says_so_and_does_not_fold(qtbot: QtBot) -> None:
    tree = _tree(qtbot, kind=BrowserSection.NETWORK)

    click_caption(tree, "On the network")

    assert not is_folded(tree, "On the network")
    assert "No new cameras" in texts(tree)
    assert caption(tree, "On the network").text() == "On the network"


def test_a_double_click_on_a_row_asks_to_open_it_and_to_the_side_with_ctrl(qtbot: QtBot) -> None:
    row = WorkspaceBrowserRow(row_id="gate", label="gate.mp4", icon_kind="video")
    tree = _tree(qtbot, row, kind=BrowserSection.VIDEOS)
    widget = _widget(tree, _item(tree, 0))
    requests: list[tuple[WorkspaceBrowserRow, RowAction]] = []
    tree.row_action_requested.connect(lambda requested, action: requests.append((requested, action)))

    for modifier in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier):
        position = QPoint(widget.width() // 3, widget.height() // 2)
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, modifier, pos=position)
        QTest.mouseDClick(widget, Qt.MouseButton.LeftButton, modifier, pos=position)

    assert requests == [(row, RowAction.OPEN), (row, RowAction.OPEN_TO_SIDE)]


def test_keys_on_the_selected_row_ask_for_its_action_and_on_a_section_caption_for_none(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _playlist(0))
    requested: list[RowAction] = []
    tree.row_action_requested.connect(lambda _row, action: requested.append(action))
    caption_item = tree.topLevelItem(0)
    assert caption_item is not None

    tree.setCurrentItem(caption_item)
    QTest.keyClick(tree, Qt.Key.Key_Delete)
    assert requested == []

    tree.setCurrentItem(_item(tree, 0))
    for key, modifier in (
        (Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier),
        (Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_F2, Qt.KeyboardModifier.NoModifier),
        (Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier),
    ):
        QTest.keyClick(tree, key, modifier)

    assert requested == [RowAction.OPEN, RowAction.OPEN_TO_SIDE, RowAction.RENAME, RowAction.REMOVE]

    requested.clear()
    _item(tree, 0).setSelected(False)  # As Ctrl-clicking a selected row does: still current, no longer selected.
    QTest.keyClick(tree, Qt.Key.Key_Delete)
    assert requested == []


def test_the_row_menu_lists_every_offered_action_greys_out_the_disabled_ones_with_their_reason_and_picks(
    qtbot: QtBot,
) -> None:
    row = WorkspaceBrowserRow(
        row_id="clip",
        label="clip.mp4",
        icon_kind="video",
        actions=(
            OfferedAction(RowAction.OPEN),
            OfferedAction(RowAction.EXPORT_VIDEO, "Open it first"),
            OfferedAction(RowAction.REMOVE),
        ),
    )
    picked: list[RowAction] = []
    parent = QWidget()
    qtbot.addWidget(parent)
    menu = build_row_menu(row, parent, picked.append)

    entries = {action.text(): action for action in menu.actions() if not action.isSeparator()}
    entries["Remove"].trigger()

    assert list(entries) == ["Open", "Export video", "Remove"]
    assert [action.isEnabled() for action in entries.values()] == [True, False, True]
    assert entries["Export video"].toolTip() == "Open it first"
    assert picked == [RowAction.REMOVE]


def test_a_rows_action_reports_the_row(qtbot: QtBot) -> None:
    playlist = _playlist(1)
    tree = _tree(qtbot, playlist)
    actions: list[tuple[WorkspaceBrowserRow, RowAction]] = []
    tree.row_action_requested.connect(lambda row, action: actions.append((row, action)))

    [rescan] = _widget(tree, _item(tree, 0)).findChildren(SmallButton)
    rescan.click()

    assert actions == [(playlist, RowAction.RESCAN)]


def _menu_entries(qtbot: QtBot, row: WorkspaceBrowserRow) -> list[str]:
    """Return the texts of *row*'s menu in order, with a separator as ``"-"``."""
    parent = QWidget()
    qtbot.addWidget(parent)
    return [
        "-" if action.isSeparator() else action.text()
        for action in build_row_menu(row, parent, lambda _action: None).actions()
    ]


def test_the_menu_splits_open_inspect_visibility_and_edit_actions_with_separators(qtbot: QtBot) -> None:
    entry = WorkspaceBrowserRow(
        row_id="gate",
        label="gate.mp4",
        icon_kind="video",
        actions=tuple(
            OfferedAction(action)
            for action in (
                RowAction.OPEN,
                RowAction.OPEN_TO_SIDE,
                RowAction.RESCAN,
                RowAction.INFORMATION,
                RowAction.EXPORT_VIDEO,
                RowAction.HIDE,
                RowAction.RENAME,
                RowAction.REMOVE,
            )
        ),
    )

    assert _menu_entries(qtbot, entry) == [
        "Open",
        "Open to the Side",
        "Rescan",
        "-",
        "Information",
        "Export video",
        "-",
        "Hide",
        "-",
        "Rename",
        "Remove",
    ]
    bare = replace(entry, actions=(OfferedAction(RowAction.RESCAN), OfferedAction(RowAction.UNHIDE)))
    assert _menu_entries(qtbot, bare) == ["Rescan", "-", "Unhide"]


def test_choosing_hide_in_the_menu_asks_for_that_action(qtbot: QtBot) -> None:
    entry = WorkspaceBrowserRow(
        row_id="entry", label="run", icon_kind="video", actions=(OfferedAction(RowAction.HIDE),)
    )
    picked: list[RowAction] = []
    parent = QWidget()
    qtbot.addWidget(parent)

    [hide] = build_row_menu(entry, parent, picked.append).actions()
    hide.trigger()

    assert picked == [RowAction.HIDE]


@pytest.mark.usefixtures("restore_app_appearance")
@pytest.mark.parametrize("text_size", [13, 21])
def test_two_line_rows_keep_both_lines_and_their_action_inside_a_narrow_tree(qtbot: QtBot, text_size: int) -> None:
    apply_text_size(text_size)
    camera = WorkspaceBrowserRow(
        row_id="camera",
        label="Loading dock entrance, north side",
        icon_kind="live_video",
        detail="192.0.2.10 · head 2 (1)",
        actions=(OfferedAction(RowAction.STOP_STREAM),),
        is_open=True,
    )
    tree = _tree(
        qtbot,
        camera,
        replace(camera, row_id="other", actions=(OfferedAction(RowAction.START_STREAM),)),
        kind=BrowserSection.LIVE,
    )
    tree.resize(220, 400)
    qtbot.waitUntil(lambda: all(_widget(tree, _item(tree, i)).width() <= 220 for i in range(2)), timeout=2000)

    for index in range(2):
        item = _item(tree, index)
        widget = _widget(tree, item)
        assert tree.visualItemRect(item).height() >= widget.sizeHint().height()
        name, detail = widget.findChildren(ElidedLabel)
        [button] = widget.findChildren(SmallButton)
        assert all(label.height() >= label.sizeHint().height() for label in (name, detail))
        assert detail.geometry().top() >= name.geometry().bottom(), "the detail is the second line"
        assert widget.rect().contains(button.geometry()), "the action stays inside the row"
        assert button.width() >= button.sizeHint().width()


def test_hovering_any_text_of_a_row_shows_the_rows_tooltip(qtbot: QtBot) -> None:
    row = WorkspaceBrowserRow(
        row_id="archive",
        label="Archive",
        icon_kind="unavailable",
        unavailable_reason="Playlist resolver is not installed",
        detail="Unavailable",
    )
    tree = _tree(qtbot, row)

    widget = _widget(tree, _item(tree, 0))

    assert "is not installed" in widget.toolTip()
    labels = widget.findChildren(QLabel)
    assert labels, "the row shows text"
    assert not any(label.toolTip() for label in labels), "a label tooltip would hide the row's"


def _camera(
    host: str, *, label: str = "", detail: str = "", add: Callable[[], None] | None = None
) -> WorkspaceBrowserRow:
    return WorkspaceBrowserRow(
        row_id=f"camera/{host}",
        label=label or host,
        icon_kind="live_video",
        detail=detail,
        actions=(OfferedAction(RowAction.ADD),),
        add=add,
    )


def _network(*cameras: WorkspaceBrowserRow) -> tuple[WorkspaceBrowserSection, ...]:
    return (WorkspaceBrowserSection(BrowserSection.NETWORK, cameras),)


def _hosts(*hosts: str) -> tuple[WorkspaceBrowserSection, ...]:
    return _network(*(_camera(host) for host in hosts))


def test_camera_add_buttons_keep_their_width_when_rows_are_narrow(qtbot: QtBot) -> None:
    cameras = (
        _camera("10.0.0.1", label="Gate"),
        _camera("10.0.0.2", label="North warehouse loading dock camera", detail="192.0.2.2 · signed in"),
    )
    tree = _tree(qtbot, *cameras, kind=BrowserSection.NETWORK)
    tree.resize(220, 400)
    qtbot.waitUntil(lambda: row(tree, cameras[0].label).width() <= 220, timeout=2000)

    buttons = [row(tree, camera.label).findChild(SmallButton) for camera in cameras]

    assert all(button is not None for button in buttons)
    assert len({button.width() for button in buttons if button is not None}) == 1
    assert all(button.width() == button.sizeHint().width() for button in buttons if button is not None)


def test_keyboard_focus_stays_on_a_rows_action_when_cameras_are_found_around_it(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _camera("10.0.0.2"), kind=BrowserSection.NETWORK)
    [button] = row(tree, "10.0.0.2").findChildren(SmallButton)
    button.setFocus()
    assert tree.focusWidget() is button
    tree.set_sections(_hosts("10.0.0.1", "10.0.0.2", "10.0.0.3"))

    assert titles(tree) == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    QApplication.processEvents()  # Deferred row updates must not take the focus away either.
    assert tree.focusWidget() is button


def test_pressing_add_after_an_update_runs_the_add_the_row_has_now(qtbot: QtBot) -> None:
    calls: list[str] = []
    tree = _tree(qtbot, _camera("10.0.0.2", add=lambda: calls.append("stale")), kind=BrowserSection.NETWORK)
    pressed: list[WorkspaceBrowserRow] = []
    tree.row_action_requested.connect(lambda row, _action: pressed.append(row))

    tree.set_sections(_network(_camera("10.0.0.2", add=lambda: calls.append("current"))))
    press(tree, "10.0.0.2")

    [requested] = pressed
    assert requested.add is not None
    requested.add()
    assert calls == ["current"]


def test_cameras_inserted_removed_and_resorted_are_listed_in_the_new_order(qtbot: QtBot) -> None:
    tree = _tree(qtbot, *_hosts("10.0.0.1", "10.0.0.3", "10.0.0.5")[0].rows, kind=BrowserSection.NETWORK)

    tree.set_sections(_hosts("10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.5"))
    assert titles(tree) == ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.5"]

    tree.set_sections(_hosts("10.0.0.1", "10.0.0.2", "10.0.0.5"))
    assert titles(tree) == ["10.0.0.1", "10.0.0.2", "10.0.0.5"]

    tree.set_sections(_hosts("10.0.0.5", "10.0.0.2", "10.0.0.1"))
    assert titles(tree) == ["10.0.0.5", "10.0.0.2", "10.0.0.1"]


def test_a_camera_whose_label_or_detail_changes_shows_the_new_text(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _camera("10.0.0.2", detail="AXIS Q1"), kind=BrowserSection.NETWORK)

    tree.set_sections(_network(_camera("10.0.0.2", label="Gate", detail="AXIS Q2")))

    assert [row_texts(shown) for shown in shown_rows(tree)] == [["Gate", "AXIS Q2"]]
