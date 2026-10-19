from __future__ import annotations

from collections.abc import Callable

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.application_shell.settings_dialog import SettingsDialog
from ax_devil.modules.chrome import BaseDialog, ChromeWindow
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.shortcuts.shortcuts_dialog import ShortcutsDialog
from tests.helpers.shortcuts import make_shortcut_manager

_Built = tuple[list[QWidget], list[QWidget]]  # Windows that own the children, and the children to check.


def _direct_window_border(window: QWidget) -> QWidget | None:
    """Return the direct custom-frame border child for a window."""
    return window.findChild(QWidget, "AxDevilWindowBorder", Qt.FindChildOption.FindDirectChildrenOnly)


def _explicit_child_window(qtbot: QtBot, _catalogs: SceneRenderCatalogManager, custom_frame: bool) -> _Built:
    """A child window whose frame is chosen explicitly, regardless of its parent's."""
    parent = ChromeWindow(use_custom_frame=True)
    qtbot.addWidget(parent)
    child = ChromeWindow(parent=parent, use_custom_frame=custom_frame, show_custom_frame_border=custom_frame)
    return [parent], [child]


def _dialog_window(qtbot: QtBot, catalogs: SceneRenderCatalogManager, custom_frame: bool) -> _Built:
    """A dialog follows its parent main window's frame."""
    window = MainWindow(
        shortcut_manager=make_shortcut_manager(), render_catalog_manager=catalogs, use_custom_frame=custom_frame
    )
    qtbot.addWidget(window)
    assert not window.shows_custom_frame_border
    assert _direct_window_border(window) is None
    dialog = BaseDialog(parent=window, title="Shared Chrome")
    return [window], [dialog]


def _nested_shortcut_editor(qtbot: QtBot, _catalogs: SceneRenderCatalogManager, custom_frame: bool) -> _Built:
    """A dialog opened from another dialog keeps the frame policy of the window above both."""
    parent = ChromeWindow(use_custom_frame=custom_frame)
    qtbot.addWidget(parent)
    settings = SettingsDialog(parent)
    return [parent, settings], [ShortcutsDialog(make_shortcut_manager(), settings)]


@pytest.mark.parametrize("custom_frame", [True, False])
@pytest.mark.parametrize(
    "build",
    [_explicit_child_window, _dialog_window, _nested_shortcut_editor],
    ids=lambda build: build.__name__,
)
def test_child_windows_get_the_expected_frame(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    build: Callable[..., _Built],
    custom_frame: bool,
) -> None:
    _owners, children = build(qtbot, render_catalog_manager, custom_frame)  # Owners must outlive the checks.
    for child in children:
        assert bool(child.windowFlags() & Qt.WindowType.FramelessWindowHint) is custom_frame
        assert (child.findChild(QWidget, "AxDevilTitleBar") is not None) is custom_frame
        assert (_direct_window_border(child) is not None) is custom_frame
