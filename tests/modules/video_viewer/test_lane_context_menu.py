"""Right-clicking a video lane offers lane-level actions, the window's own actions among them."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QAction, QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.video_viewer.offline_viewer_runtime import OfflineLane
from ax_devil.modules.workspace.core import PlaylistContent, RecentWorkspaces
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from tests.helpers.shortcuts import make_shortcut_manager
from tests.helpers.workspace import FakePrompts, content_item, inline_resolver
from tests.modules.video_viewer.test_offline_video_viewer import (  # noqa: F401 - the stubs of media opening
    _background,
    _lane_entry,
    _make_local_content,
    _stub_spec_source_opening,
)

pytestmark = pytest.mark.usefixtures("_background", "_stub_spec_source_opening")

_EXPORT_DIALOG = "ax_devil.modules.video_viewer.export.ExportDialog"
_HIDE = "Hide"


class _Open(NamedTuple):
    session: WorkspaceSession
    viewer: OfflineVideoViewerWidget
    manager: ShortcutManager

    @property
    def lanes(self) -> list[OfflineLane]:
        assert self.viewer._runtime is not None
        return self.viewer._runtime.lanes


@pytest.fixture()
def opened(qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path) -> _Open:
    """A shown session with a viewer open on one entry of two lanes, A and B, and the window's shortcut actions."""
    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        resolver=inline_resolver(),
        recent_workspaces=RecentWorkspaces(lambda: tmp_path / "state" / "recent-workspaces.json"),
        prompts=FakePrompts(),
    )
    window = session.widget()
    qtbot.addWidget(window)
    manager = make_shortcut_manager()
    manager.install(window)
    session.set_shortcut_manager(manager)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    playlist = PlaylistContent(
        display_name="multi", entries=(_lane_entry(_make_local_content("A"), _make_local_content("B")),)
    )
    session.add_items([content_item(playlist)])
    viewer = session.focused_widget()
    assert isinstance(viewer, OfflineVideoViewerWidget)
    result = _Open(session, viewer, manager)
    qtbot.waitUntil(lambda: len(result.lanes) == 2 and all(lane.export_action.isEnabled() for lane in result.lanes))
    return result


def _right_click(display: FrameDisplay) -> None:
    point = QPoint(5, 5)
    viewport = display.viewport
    QApplication.sendEvent(
        viewport, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, viewport.mapToGlobal(point))
    )


def _right_click_lane(opened: _Open, lane: OfflineLane, act: Callable[[QMenu], None]) -> None:
    """Right-click *lane*; *act* sees the menu while it is open, and the menu is closed afterwards."""

    def inspect() -> None:
        menu = opened.viewer.window().findChildren(QMenu)[-1]
        act(menu)
        menu.close()

    QTimer.singleShot(0, inspect)
    _right_click(lane.display)


def _lane_rows(session: WorkspaceSession) -> list[WorkspaceBrowserRow]:
    """The sidebar rows of the lanes of the one playlist entry."""
    (section,) = (s for s in session._workspace_controller.sections() if s.rows)
    (playlist_row,) = section.rows
    (entry_row,) = playlist_row.children
    return list(entry_row.children)


def _hide(menu: QMenu) -> QAction:
    return next(entry for entry in menu.actions() if entry.text() == _HIDE)


def test_the_lane_menu_offers_export_the_windows_actions_and_leaving_the_lane_out(opened: _Open) -> None:
    listed: list[QAction] = []

    _right_click_lane(
        opened, opened.lanes[1], lambda menu: listed.extend(a for a in menu.actions() if not a.isSeparator())
    )

    texts = [action.text() for action in listed]
    assert texts[0] == "Export video"
    assert opened.manager.get_action("view.add_bookmark").text() in texts
    assert texts[-1] == _HIDE
    assert "sidebar" in listed[-1].toolTip()


def test_right_clicking_a_lane_focuses_it_before_the_menu_shows_and_export_targets_it(opened: _Open) -> None:
    first, second = opened.lanes
    first.display.viewport.setFocus()
    assert opened.viewer._runtime.focused_display() is first.display  # type: ignore[union-attr]
    focused: list[object] = []

    def act(menu: QMenu) -> None:
        focused.append(opened.viewer._runtime.focused_display())  # type: ignore[union-attr]
        with patch(_EXPORT_DIALOG) as dialog_type:
            second.export_action.trigger()
        focused.append([lane.name for lane in dialog_type.call_args.args[0]])

    _right_click_lane(opened, second, act)

    assert focused == [second.display, ["B"]]


def test_leaving_a_lane_out_from_its_menu_changes_the_state_the_sidebar_shows(opened: _Open, qtbot: QtBot) -> None:
    session, viewer = opened.session, opened.viewer
    before = [row.is_considered for row in _lane_rows(session)]

    _right_click_lane(opened, opened.lanes[1], lambda menu: _hide(menu).trigger())

    rows = _lane_rows(session)
    assert before == [True, True]
    assert [row.is_considered for row in rows] == [True, False]
    assert rows[1].consideration_ref is not None
    assert not session._workspace_store.is_item_considered(rows[1].consideration_ref)
    qtbot.waitUntil(lambda: [lane.name for lane in opened.lanes] == ["A"])
    assert viewer is session.focused_widget()
