"""Every window and dialog shows its button and label text in full, in both themes and at small and large text.

A new `BaseDialog` or `ChromeWindow` subclass must be added to `SURFACES` (checked here) or to `EXEMPT` with the
reason it is covered elsewhere; the guard test fails until it is.
"""

import ast
import importlib
import pkgutil
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

import ax_devil
from ax_devil.modules.application_shell.about_dialog import AboutDialog
from ax_devil.modules.application_shell.quick_setup_dialog import QuickSetupDialog
from ax_devil.modules.application_shell.settings_dialog import SettingsDialog
from ax_devil.modules.catalog_viewer.window import NewCatalogDialog
from ax_devil.modules.chrome import BaseDialog, ChromeWindow
from ax_devil.modules.chrome.choice_dialog import ChoiceDialog
from ax_devil.modules.chrome.theme import apply_text_size, apply_theme
from ax_devil.modules.plugin_manager.add_plugin_dialog import AddPluginDialog
from ax_devil.modules.shortcuts.shortcuts_dialog import ShortcutsDialog
from ax_devil.modules.workspace.core.item_info import WorkspaceItemInfo
from ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog import AddLiveStreamDialog
from ax_devil.modules.workspace.ui.add_content.add_video_dialog import AddVideoDialog
from ax_devil.modules.workspace.ui.item_info_dialog import WorkspaceItemInfoDialog
from ax_devil.modules.workspace.ui.rename_dialog import RenameDialog
from tests.helpers.clipping import clipped_text_on_every_page
from tests.helpers.shortcuts import make_shortcut_manager

SURFACES: dict[type[QWidget], Callable[[], QWidget]] = {
    AboutDialog: AboutDialog,
    AddLiveStreamDialog: AddLiveStreamDialog,
    AddPluginDialog: AddPluginDialog,
    AddVideoDialog: AddVideoDialog,
    NewCatalogDialog: lambda: NewCatalogDialog("Source"),
    QuickSetupDialog: QuickSetupDialog,
    RenameDialog: lambda: RenameDialog("Name"),
    SettingsDialog: SettingsDialog,
    ShortcutsDialog: lambda: ShortcutsDialog(make_shortcut_manager()),
    WorkspaceItemInfoDialog: lambda: WorkspaceItemInfoDialog(WorkspaceItemInfo("Information", (("Field", "Value"),))),
    ChoiceDialog: lambda: ChoiceDialog(
        "Save Workspace",
        "Save changes to Quarterly review of the north entrance cameras, second pass?",
        {"Save": 1, "Close without Saving": 2, "Cancel": 3},
        1,
    ),
}

EXEMPT = {
    "AddPlaylistDialog": "needs a playlist resolver; its fields are covered in the add-content tests",
    "CatalogViewerWindow": "needs a catalog manager; covered in test_catalog_viewer_window",
    "DebugWindow": "covered in test_debug_window",
    "ExportDialog": "needs export lanes; sized and fitted in test_export",
    "MainWindow": "needs the whole application; covered by the UI screenshots",
    "_FullscreenWindow": "only exists around a playing video",
}


@pytest.mark.usefixtures("restore_app_appearance")
@pytest.mark.parametrize("surface", SURFACES, ids=lambda surface: surface.__name__)
def test_surface_shows_its_text_in_full(qtbot: QtBot, surface: type[QWidget]) -> None:
    """No button or label is narrower than its text in either theme, at a small and a large text size."""
    for mode, size in (("dark", 13), ("light", 21)):
        apply_theme(mode)
        apply_text_size(size)
        window = SURFACES[surface]()
        qtbot.addWidget(window)
        window.show()
        QApplication.processEvents()
        assert clipped_text_on_every_page(window) == [], (mode, size)
        window.close()


def _window_classes() -> set[str]:
    """Return the name of every `BaseDialog` and `ChromeWindow` subclass in the application."""
    for module in pkgutil.walk_packages(ax_devil.__path__, "ax_devil."):
        importlib.import_module(module.name)
    found = set()
    pending: list[type[QWidget]] = [BaseDialog, ChromeWindow]
    while pending:
        for subclass in pending.pop().__subclasses__():
            if subclass.__module__.startswith("ax_devil."):  # tests define stand-in windows of their own
                found.add(subclass.__name__)
            pending.append(subclass)
    return found


def test_every_window_is_checked_or_exempt() -> None:
    """A new window or dialog cannot be added without deciding how its text fit is checked."""
    known = {surface.__name__ for surface in SURFACES} | set(EXEMPT)
    assert _window_classes() - known == set(), "add the new window to SURFACES or give it an EXEMPT reason"


def test_questions_use_the_choice_dialog() -> None:
    """A `QMessageBox` squeezes its buttons under the theme's padding; questions are asked with `choice_dialog.ask`.

    The last-resort error box in `exception_handler` stays a plain `QMessageBox` so it needs none of the app's chrome.
    """
    source = Path(ax_devil.__file__).parent
    offenders = []
    for path in source.rglob("*.py"):
        if path.name == "exception_handler.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                banned = func.id == "QMessageBox"
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                banned = func.value.id == "QMessageBox" and func.attr in {"question", "information", "critical"}
            else:
                banned = False
            if banned:
                offenders.append(f"{path.relative_to(source)}:{node.lineno}")
    assert offenders == []
