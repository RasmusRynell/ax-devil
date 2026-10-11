"""The sidebar hosts one view at a time, chosen from the activity bar."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import ListRow
from ax_devil.modules.chrome.sidebar_view import SidebarView, ViewGroup
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.workspace.ui.activity_bar import ActivityBar
from ax_devil.modules.workspace.ui.application_window import ApplicationWindow
from ax_devil.modules.workspace.ui.sidebar_panel import SidebarPanel
from ax_devil.modules.workspace.ui.split_view import SplitView


def _view(view_id: str, group: ViewGroup) -> SidebarView:
    return SidebarView(view_id, view_id.title(), Icon.INFO, group, QLabel(view_id))


@pytest.fixture
def window(qtbot: QtBot) -> ApplicationWindow:
    app_window = ApplicationWindow(sidebar=SidebarPanel(), center_area=SplitView())
    qtbot.addWidget(app_window)
    app_window.resize(1000, 600)
    app_window.show()
    for view in (
        _view("plugins", ViewGroup.APP),
        _view("bookmarks", ViewGroup.REVIEW),
        _view("workspace", ViewGroup.WORKSPACE),
        _view("live", ViewGroup.SOURCES),
    ):
        app_window.add_view(view)
    return app_window


def _button_order(bar: ActivityBar) -> list[str]:
    """Return the view ids of the bar's buttons from top to bottom."""
    ids = [view.view_id for view, _ in bar._views]
    return sorted(ids, key=lambda view_id: bar.view_button(view_id).y())


def test_buttons_follow_their_groups_whatever_order_the_views_were_added_in(window: ApplicationWindow) -> None:
    assert _button_order(window.activity_bar()) == ["workspace", "live", "bookmarks", "plugins"]


def test_app_views_sit_below_the_view_buttons_at_the_bottom_of_the_bar(window: ApplicationWindow) -> None:
    bar = window.activity_bar()
    assert bar.view_button("plugins").y() > bar.view_button("bookmarks").y() + bar.view_button("bookmarks").height()
    assert bar.view_button("plugins").y() + bar.view_button("plugins").height() <= bar.height()


def test_the_first_view_is_shown_and_its_button_checked(window: ApplicationWindow) -> None:
    bar = window.activity_bar()
    assert window.is_sidebar_shown()
    assert [bar.view_button(view_id).isChecked() for view_id in ("workspace", "live", "bookmarks", "plugins")] == [
        False,
        False,
        False,
        True,
    ]  # "plugins" was added first.


def test_clicking_another_view_switches_and_clicking_the_current_one_hides_the_sidebar(
    window: ApplicationWindow,
) -> None:
    bar = window.activity_bar()
    bar.view_button("live").click()
    assert window.is_sidebar_shown() and bar.view_button("live").isChecked()
    assert not bar.view_button("plugins").isChecked()

    bar.view_button("live").click()
    assert not window.is_sidebar_shown() and not bar.view_button("live").isChecked()

    bar.view_button("bookmarks").click()
    assert window.is_sidebar_shown() and bar.view_button("bookmarks").isChecked()


def test_toggling_the_sidebar_keeps_its_view(window: ApplicationWindow) -> None:
    window.show_view("bookmarks")
    window.toggle_sidebar()
    window.toggle_sidebar()
    assert window.activity_bar().view_button("bookmarks").isChecked()


def test_showing_a_view_reveals_a_hidden_sidebar(window: ApplicationWindow) -> None:
    window.toggle_sidebar()
    assert not window.is_sidebar_shown()
    window.show_view("workspace")
    assert window.is_sidebar_shown() and window.activity_bar().view_button("workspace").isChecked()


def test_a_second_view_with_the_same_id_is_refused(qtbot: QtBot) -> None:
    sidebar = SidebarPanel()
    qtbot.addWidget(sidebar)
    sidebar.add_view(_view("live", ViewGroup.SOURCES))
    with pytest.raises(ValueError, match="live"):
        sidebar.add_view(_view("live", ViewGroup.SOURCES))


def test_a_click_on_a_row_activates_it_but_a_click_on_its_button_does_not(qtbot: QtBot) -> None:
    clicks: list[str] = []
    button = SmallButton("Add")
    button.clicked.connect(lambda: clicks.append("button"))
    row = ListRow("Entrance", trailing=[button])
    row.activated.connect(lambda: clicks.append("row"))
    qtbot.addWidget(row)
    row.resize(300, 40)
    row.show()

    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert clicks == ["button"]
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(row.width() // 2, row.height() // 2))
    assert clicks == ["button", "row"]
