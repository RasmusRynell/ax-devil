"""The Live view lists, searches, sorts and filters held and found cameras, and edits the logins it signs in with."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QLineEdit, QMenu, QScrollArea
from pytestqt.qtbot import QtBot

from ax_devil.modules.device_discovery.network_scan import DiscoveredCamera, Login, SignIn
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.workspace.core import LiveStreamItem
from ax_devil.modules.workspace.ui.logins_editor import LoginsEditor
from tests.helpers.live_view import Live, scan
from tests.helpers.section_tree import captions, is_folded
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
    assert list(live.rows()) == ["Entrance", "Entrance/Video only"], "a camera that matches shows its streams"

    live.search("192.0.2.10")
    assert list(live.rows()) == ["Entrance", "Entrance/Video only"]

    live.search("")
    assert list(live.rows()) == ["Entrance", "Entrance/Video only", "192.0.2.12", "192.0.2.13"]


def test_held_and_found_cameras_follow_the_chosen_order(live: Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"), LiveStreamItem(host="192.0.2.11"))
    scan(
        live,
        DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", "12.10.1", uptime_seconds=60.0),
        DiscoveredCamera("192.0.2.11", "AXIS M3086-V", "", "12.9.4", uptime_seconds=600.0),
        DiscoveredCamera("192.0.2.12", "AXIS Q1656", "", "12.10.1", uptime_seconds=60.0),
        DiscoveredCamera("192.0.2.13", "AXIS M3086-V", "", "12.9.4", uptime_seconds=600.0),
    )
    assert live.cameras() == ["Entrance", "192.0.2.11", "192.0.2.12", "192.0.2.13"]

    live.choose("Sort", "Model")
    assert live.cameras() == ["192.0.2.11", "Entrance", "192.0.2.13", "192.0.2.12"]
    assert list(live.rows())[:2] == ["192.0.2.11", "192.0.2.11/Video only"], "streams move with their camera"

    live.choose("Sort", "Uptime")
    assert live.cameras() == ["Entrance", "192.0.2.11", "192.0.2.12", "192.0.2.13"]


def test_the_chosen_order_stays_while_cameras_are_found(live: Live) -> None:
    scan(live, DiscoveredCamera("192.0.2.12", "AXIS Q1656", ""))
    live.choose("Sort", "Model")

    scan(live, DiscoveredCamera("192.0.2.12", "AXIS Q1656", ""), DiscoveredCamera("192.0.2.13", "AXIS M3086-V", ""))

    assert list(live.rows()) == ["192.0.2.13", "192.0.2.12"]


def test_the_add_stream_button_heads_the_live_section_while_it_is_empty_and_while_streams_run(live: Live) -> None:
    assert live.button("Add stream").isVisibleTo(live.view)
    assert captions(live.view.tree) == ["Live, 0"]

    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"))
    live.press("Entrance/Video only")
    assert live.streams() == {"Entrance/Video only": "Stop stream"}

    live.button("Add stream").click()

    assert live.add_stream_requests == [None]
    assert not is_folded(live.view.tree, "Live"), "the button does not fold the section"


def test_the_filter_hides_found_cameras_by_type_and_the_caption_counts_them(live: Live) -> None:
    scan(
        live,
        DiscoveredCamera("192.0.2.12", "AXIS Q1656", "", product_type="Box Camera"),
        DiscoveredCamera("192.0.2.13", "AXIS C1310-E", "", product_type="Network Speaker"),
    )

    live.choose("Filter", "Network Speaker")

    assert list(live.rows()) == ["192.0.2.12"]
    assert captions(live.view.tree) == ["Live, 0", "On the network, 2, 1 filtered out"]

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

    assert list(live.rows()) == ["Entrance", "Entrance/Video only"]


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
    assert "Credentials refused" in live.row("192.0.2.13").toolTip()
    assert live.rows()["192.0.2.13"] == "", "a camera that did not accept a login cannot be added"
    assert "Not signed in" in live.row("192.0.2.14").toolTip(), "a camera never tried says why"
    assert "Not signed in" not in live.row("192.0.2.12").toolTip()
    live.press("192.0.2.12")
    [signed_in] = live.store.workspace.items
    assert isinstance(signed_in, LiveStreamItem)
    assert (signed_in.username, signed_in.password) == ("operator", "$OPERATOR_PASS"), "a reference stays one"


def test_a_found_camera_opens_its_information_and_hides_its_password(live: Live) -> None:
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

    assert live.detail("192.0.2.12") == "AXIS P3265-LVE"
    live.click("192.0.2.12")

    assert live.detail("192.0.2.12") == (
        "AXIS P3265-LVE · 192.0.2.12 · Dome Camera · Firmware 12.6.85 · Serial ACCC8E000002 · Up 1d 2h"
    )
    hover = live.row("192.0.2.12").toolTip()
    assert "aarch64" in hover and "Ambarella CV25" in hover
    assert "never-display-this" not in hover and "Signed in" not in hover


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
    live.press("Entrance/Video only")
    tree = live.view.tree
    tree.window().show()
    entries: list[str] = []

    def right_click(name: str, choose: str = "") -> None:
        widget = live.row(name)

        def pick() -> None:
            menu = tree.findChildren(QMenu)[-1]
            entries[:] = [action.text() for action in menu.actions() if not action.isSeparator()]
            menu.close()
            for action in menu.actions():
                if action.text() == choose:
                    action.trigger()

        QTimer.singleShot(0, pick)
        tree.customContextMenuRequested.emit(tree.viewport().mapFromGlobal(widget.mapToGlobal(widget.rect().center())))

    right_click("Entrance")
    assert entries == ["Add stream"], "a camera row offers only to add a stream"

    right_click("Entrance/Video only", "Remove")

    assert entries[0] == "Stop stream"
    assert "Edit" in entries and "Remove" in entries
    assert live.streams() == {"192.0.2.11/Video only": "Start stream"}
    assert live.center.get_widget_count() == 0, "removing a stream stops it"
