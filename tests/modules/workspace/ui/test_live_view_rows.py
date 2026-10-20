"""The Live view lists, searches, sorts and filters held and found cameras, and edits the logins it signs in with."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QScrollArea, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.device_discovery.network_scan import DiscoveredCamera, Login, SignIn
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.workspace.core import LiveStreamItem
from ax_devil.modules.workspace.ui.logins_editor import LoginsEditor
from tests.helpers.live_view import Live, scan
from tests.helpers.section_tree import captions, row
from tests.helpers.widgets import button


def test_search_finds_workspace_and_found_cameras_by_what_they_show_and_their_details(live: Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"))
    scan(
        live,
        DiscoveredCamera("192.0.2.12", "AXIS P3265-LVE", "ACCC8E000002", "12.6.85"),
        DiscoveredCamera("192.0.2.13", "AXIS Q1656", "ACCC8E000003", "11.11.73"),
    )

    live.search("ACCC8E000003")
    assert list(live.rows()) == ["192.0.2.13"]

    live.search("12.6")
    assert list(live.rows()) == ["192.0.2.12"]

    live.search("entrance")
    assert list(live.rows()) == ["Entrance"]

    live.search("")
    assert list(live.rows()) == ["Entrance", "192.0.2.12", "192.0.2.13"]


def test_held_and_found_cameras_follow_the_chosen_order(live: Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"), LiveStreamItem(host="192.0.2.11"))
    scan(
        live,
        DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", "12.10.1", uptime_seconds=60.0),
        DiscoveredCamera("192.0.2.11", "AXIS M3086-V", "", "12.9.4", uptime_seconds=600.0),
        DiscoveredCamera("192.0.2.12", "AXIS Q1656", "", "12.10.1", uptime_seconds=60.0),
        DiscoveredCamera("192.0.2.13", "AXIS M3086-V", "", "12.9.4", uptime_seconds=600.0),
    )
    assert list(live.rows()) == ["Entrance", "192.0.2.11", "192.0.2.12", "192.0.2.13"]

    live.choose("Sort", "Model")
    assert list(live.rows()) == ["192.0.2.11", "Entrance", "192.0.2.13", "192.0.2.12"]

    live.choose("Sort", "Uptime")
    assert list(live.rows()) == ["Entrance", "192.0.2.11", "192.0.2.12", "192.0.2.13"]


def test_the_chosen_order_stays_while_cameras_are_found(live: Live) -> None:
    scan(live, DiscoveredCamera("192.0.2.12", "AXIS Q1656", ""))
    live.choose("Sort", "Model")

    scan(live, DiscoveredCamera("192.0.2.12", "AXIS Q1656", ""), DiscoveredCamera("192.0.2.13", "AXIS M3086-V", ""))

    assert list(live.rows()) == ["192.0.2.13", "192.0.2.12"]


def test_add_manually_and_the_scan_settings_sit_under_the_tree(live: Live) -> None:
    live.view.resize(360, 700)
    live.view.show()
    QApplication.processEvents()

    def top(widget: QWidget) -> int:
        return widget.mapTo(live.view, QPoint(0, 0)).y()

    add_manually, rescan = live.button("Add manually"), live.button("Rescan")
    assert top(live.view.tree) < top(add_manually) < top(rescan)
    with live.qtbot.waitSignal(live.view.add_manually_requested, timeout=1000):
        add_manually.click()


def test_the_filter_hides_found_cameras_by_type_and_the_caption_counts_them(live: Live) -> None:
    scan(
        live,
        DiscoveredCamera("192.0.2.12", "AXIS Q1656", "", product_type="Box Camera"),
        DiscoveredCamera("192.0.2.13", "AXIS C1310-E", "", product_type="Network Speaker"),
    )

    live.choose("Filter", "Network Speaker")

    assert list(live.rows()) == ["192.0.2.12"]
    assert captions(live.view.tree) == ["On the network, 2, 1 filtered out"]

    live.choose("Filter", "Network Speaker")
    assert list(live.rows()) == ["192.0.2.12", "192.0.2.13"]


def test_the_filter_hides_held_cameras_by_what_the_scan_learned_too(live: Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"), LiveStreamItem(host="192.0.2.11"))

    scan(
        live,
        DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", product_type="Box Camera"),
        DiscoveredCamera("192.0.2.11", "AXIS C1310-E", "", product_type="Network Speaker"),
    )
    live.choose("Filter", "Network Speaker")

    assert list(live.rows()) == ["Entrance"]


def test_trying_to_sign_in_scans_with_the_saved_logins_and_add_uses_the_one_that_worked(
    live: Live, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guest, operator = Login("guest", "guest"), Login("operator", "$OPERATOR_PASS")
    live.save_logins(tmp_path, monkeypatch, guest, operator)
    assert live.sign_in_choice().isChecked()

    scan(
        live,
        DiscoveredCamera("192.0.2.12", "AXIS Q1656", "", sign_in=SignIn.SIGNED_IN, login=operator),
        DiscoveredCamera("192.0.2.13", "AXIS M3086-V", "", sign_in=SignIn.REFUSED),
        DiscoveredCamera("192.0.2.14", "AXIS P3265-LVE", ""),
    )

    assert live.scanner.logins == (guest, operator)
    assert "Signed in" not in live.detail("192.0.2.12")
    assert "Credentials refused" in live.detail("192.0.2.13")
    assert live.rows()["192.0.2.13"] == "", "a camera that did not accept a login cannot be added"
    assert "Not signed in" in row(live.view.tree, "192.0.2.14").toolTip(), "a camera never tried says why"
    assert "Not signed in" not in row(live.view.tree, "192.0.2.12").toolTip()
    live.press("192.0.2.12")
    [signed_in] = live.store.workspace.items
    assert isinstance(signed_in, LiveStreamItem)
    assert (signed_in.username, signed_in.password) == ("operator", "$OPERATOR_PASS"), "a reference stays one"


def test_found_camera_row_shows_all_non_secret_information(live: Live) -> None:
    scan(
        live,
        DiscoveredCamera(
            "192.0.2.12",
            "AXIS P3265-LVE",
            "ACCC8E000002",
            "12.6.85",
            "Dome Camera",
            93_600.0,
            "aarch64",
            "Ambarella CV25",
            SignIn.SIGNED_IN,
            Login("root", "never-display-this"),
        ),
    )

    detail = live.detail("192.0.2.12")
    assert all(
        text in detail
        for text in (
            "AXIS P3265-LVE",
            "ACCC8E000002",
            "12.6.85",
            "Dome Camera",
            "1d 2h",
            "aarch64",
            "Ambarella CV25",
        )
    )
    assert "never-display-this" not in detail
    assert "Signed in" not in detail


def test_a_scan_without_sign_in_sends_no_credentials(
    live: Live, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.save_logins(tmp_path, monkeypatch, Login("root", "pass"))
    live.sign_in_choice().setChecked(False)
    scan(live, DiscoveredCamera("192.0.2.12", "AXIS Q1656", ""))
    assert live.scanner.logins == ()


def test_the_logins_show_under_sign_in_and_edits_there_are_saved_for_settings_too(
    live: Live, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.save_logins(tmp_path, monkeypatch, Login("root", "pass"))
    [editor] = live.view.findChildren(LoginsEditor)
    assert not editor.isHidden(), "sign-in and its logins are enabled by default"

    live.sign_in_choice().setChecked(False)
    assert editor.isHidden()
    live.sign_in_choice().setChecked(True)
    assert not editor.isHidden()
    button(editor, "Add login").click()
    *_, username, password = [edit for edit in editor.findChildren(QLineEdit) if edit.isVisibleTo(editor)]
    username.setText("operator")
    password.setText("secret")
    password.editingFinished.emit()

    saved = (Login("root", "pass"), Login("operator", "secret"))
    assert Login.saved(ConfigManager()) == saved, "Settings reads the same list"
    scan(live, DiscoveredCamera("192.0.2.12", "AXIS Q1656", ""))
    assert live.scanner.logins == saved


@pytest.mark.parametrize(("count", "scrolls"), [(5, False), (7, True)])
def test_up_to_five_logins_show_at_once_and_more_scroll(qtbot: QtBot, count: int, scrolls: bool) -> None:
    editor = LoginsEditor([Login(f"user{index}", "pass") for index in range(count)])
    qtbot.addWidget(editor)
    editor.show()
    QCoreApplication.processEvents()

    scroll = editor.findChild(QScrollArea)
    assert scroll is not None
    assert (scroll.verticalScrollBar().maximum() > 0) is scrolls


def test_an_empty_login_list_takes_no_room(qtbot: QtBot) -> None:
    editor = LoginsEditor([Login("root", "pass")])
    qtbot.addWidget(editor)

    editor.set_logins(())

    scroll = editor.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.height() == 0


def test_right_click_remove_takes_a_camera_out_of_the_workspace(live: Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"), LiveStreamItem(host="192.0.2.11"))
    live.press("Entrance")
    tree = live.view.tree
    tree.window().show()
    widget = row(tree, "Entrance")
    entries: list[str] = []

    def remove() -> None:
        menu = tree.findChildren(QMenu)[-1]
        entries.extend(action.text() for action in menu.actions() if not action.isSeparator())
        menu.close()
        next(action for action in menu.actions() if action.text() == "Remove").trigger()

    QTimer.singleShot(0, remove)
    tree.customContextMenuRequested.emit(tree.viewport().mapFromGlobal(widget.mapToGlobal(widget.rect().center())))

    assert entries[0] == "Stop stream"
    assert "Edit" in entries and "Remove" in entries
    assert live.rows() == {"192.0.2.11": "Start stream"}
    assert live.center.get_widget_count() == 0, "removing a camera stops its stream"
