"""The section tree reports what the user does with rows, and keeps rows usable at narrow widths."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget
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
from ax_devil.modules.workspace.ui.row_menu import build_row_menu
from tests.helpers.section_tree import press, row, row_texts, shown_rows, titles
from tests.helpers.section_tree_rows import item_at, playlist_row, row_widget, show_tree


def test_a_double_click_on_a_row_asks_to_open_it_and_to_the_side_with_ctrl(qtbot: QtBot) -> None:
    row = WorkspaceBrowserRow(row_id="gate", label="gate.mp4", icon_kind="video")
    tree = show_tree(qtbot, row, kind=BrowserSection.VIDEOS)
    widget = row_widget(tree, item_at(tree, 0))
    requests: list[tuple[WorkspaceBrowserRow, RowAction]] = []
    tree.row_action_requested.connect(lambda requested, action: requests.append((requested, action)))

    for modifier in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier):
        position = QPoint(widget.width() // 3, widget.height() // 2)
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, modifier, pos=position)
        QTest.mouseDClick(widget, Qt.MouseButton.LeftButton, modifier, pos=position)

    assert requests == [(row, RowAction.OPEN), (row, RowAction.OPEN_TO_SIDE)]


def test_keys_on_the_selected_row_ask_for_its_action_and_on_a_section_caption_for_none(qtbot: QtBot) -> None:
    tree = show_tree(qtbot, playlist_row(0))
    requested: list[RowAction] = []
    tree.row_action_requested.connect(lambda _row, action: requested.append(action))
    caption_item = tree.topLevelItem(0)
    assert caption_item is not None

    tree.setCurrentItem(caption_item)
    QTest.keyClick(tree, Qt.Key.Key_Delete)
    assert requested == []

    tree.setCurrentItem(item_at(tree, 0))
    for key, modifier in (
        (Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier),
        (Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_F2, Qt.KeyboardModifier.NoModifier),
        (Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier),
    ):
        QTest.keyClick(tree, key, modifier)

    assert requested == [RowAction.OPEN, RowAction.OPEN_TO_SIDE, RowAction.RENAME, RowAction.REMOVE]

    requested.clear()
    item_at(tree, 0).setSelected(False)  # As Ctrl-clicking a selected row does: still current, no longer selected.
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
    playlist = playlist_row(1)
    tree = show_tree(qtbot, playlist)
    actions: list[tuple[WorkspaceBrowserRow, RowAction]] = []
    tree.row_action_requested.connect(lambda row, action: actions.append((row, action)))

    [add] = row_widget(tree, item_at(tree, 0)).findChildren(SmallButton)
    add.click()

    assert actions == [(playlist, RowAction.ADD)]


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
                RowAction.ADD,
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
        "Add",
        "-",
        "Information",
        "Export video",
        "-",
        "Hide",
        "-",
        "Rename",
        "Remove",
    ]
    bare = replace(entry, actions=(OfferedAction(RowAction.ADD), OfferedAction(RowAction.UNHIDE)))
    assert _menu_entries(qtbot, bare) == ["Add", "-", "Unhide"]


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
    tree = show_tree(
        qtbot,
        camera,
        replace(camera, row_id="other", actions=(OfferedAction(RowAction.START_STREAM),)),
        kind=BrowserSection.LIVE,
    )
    tree.resize(220, 400)
    qtbot.waitUntil(lambda: all(row_widget(tree, item_at(tree, i)).width() <= 220 for i in range(2)), timeout=2000)

    for index in range(2):
        item = item_at(tree, index)
        widget = row_widget(tree, item)
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
    tree = show_tree(qtbot, row)

    widget = row_widget(tree, item_at(tree, 0))

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
    tree = show_tree(qtbot, *cameras, kind=BrowserSection.NETWORK)
    tree.resize(220, 400)
    qtbot.waitUntil(lambda: row(tree, cameras[0].label).width() <= 220, timeout=2000)

    buttons = [row(tree, camera.label).findChild(SmallButton) for camera in cameras]

    assert all(button is not None for button in buttons)
    assert len({button.width() for button in buttons if button is not None}) == 1
    assert all(button.width() == button.sizeHint().width() for button in buttons if button is not None)


def test_keyboard_focus_stays_on_a_rows_action_when_cameras_are_found_around_it(qtbot: QtBot) -> None:
    tree = show_tree(qtbot, _camera("10.0.0.2"), kind=BrowserSection.NETWORK)
    # A native desktop activates the window some time after it is shown, and activation can move the focus.
    tree.window().activateWindow()
    qtbot.waitUntil(tree.window().isActiveWindow)
    QApplication.processEvents()
    [button] = row(tree, "10.0.0.2").findChildren(SmallButton)
    button.setFocus()
    assert tree.focusWidget() is button
    tree.set_sections(_hosts("10.0.0.1", "10.0.0.2", "10.0.0.3"))

    assert titles(tree) == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    QApplication.processEvents()  # Deferred row updates must not take the focus away either.
    assert tree.focusWidget() is button


def test_pressing_add_after_an_update_runs_the_add_the_row_has_now(qtbot: QtBot) -> None:
    calls: list[str] = []
    tree = show_tree(qtbot, _camera("10.0.0.2", add=lambda: calls.append("stale")), kind=BrowserSection.NETWORK)
    pressed: list[WorkspaceBrowserRow] = []
    tree.row_action_requested.connect(lambda row, _action: pressed.append(row))

    tree.set_sections(_network(_camera("10.0.0.2", add=lambda: calls.append("current"))))
    press(tree, "10.0.0.2")

    [requested] = pressed
    assert requested.add is not None
    requested.add()
    assert calls == ["current"]


def test_cameras_inserted_removed_and_resorted_are_listed_in_the_new_order(qtbot: QtBot) -> None:
    tree = show_tree(qtbot, *_hosts("10.0.0.1", "10.0.0.3", "10.0.0.5")[0].rows, kind=BrowserSection.NETWORK)

    tree.set_sections(_hosts("10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.5"))
    assert titles(tree) == ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.5"]

    tree.set_sections(_hosts("10.0.0.1", "10.0.0.2", "10.0.0.5"))
    assert titles(tree) == ["10.0.0.1", "10.0.0.2", "10.0.0.5"]

    tree.set_sections(_hosts("10.0.0.5", "10.0.0.2", "10.0.0.1"))
    assert titles(tree) == ["10.0.0.5", "10.0.0.2", "10.0.0.1"]


def test_a_camera_whose_label_or_detail_changes_shows_the_new_text(qtbot: QtBot) -> None:
    tree = show_tree(qtbot, _camera("10.0.0.2", detail="AXIS Q1"), kind=BrowserSection.NETWORK)

    tree.set_sections(_network(_camera("10.0.0.2", label="Gate", detail="AXIS Q2")))

    assert [row_texts(shown) for shown in shown_rows(tree)] == [["Gate", "AXIS Q2"]]
