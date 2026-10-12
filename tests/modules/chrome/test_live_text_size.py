"""A window that is open while the text size changes ends up as it would have if opened at the new size.

`test_surface_fit` builds each window after choosing the size; here the size changes under an open window, as it does
when the user picks another **Text size** in Settings.
"""

from collections.abc import Callable

import pytest
from PySide6.QtWidgets import QApplication, QScrollBar, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.application_shell.settings_dialog import SettingsDialog
from ax_devil.modules.chrome.theme import apply_text_size, apply_theme
from ax_devil.modules.diagnostics.debug_window import DebugWindow
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel
from tests.helpers.clipping import clipped_text_on_every_page
from tests.helpers.shortcuts import make_shortcut_manager
from tests.modules.chrome.test_surface_fit import SURFACES

SMALL, LARGE = 13, 21
QWIDGETSIZE_MAX = 16777215

Constraint = tuple[str, int, int, int, int]


def _size_constraints(window: QWidget) -> list[Constraint]:
    """Return every explicit minimum or maximum size in *window*; a size fixed at construction shows up here."""
    unlimited = QWIDGETSIZE_MAX
    constraints = [
        (
            type(widget).__name__,
            widget.minimumWidth(),
            widget.minimumHeight(),
            widget.maximumWidth(),
            widget.maximumHeight(),
        )
        for widget in [window, *window.findChildren(QWidget)]
        # Scroll bars come and go with the content, and their containers carry their own limits.
        if not widget.objectName().startswith("qt_") and not isinstance(widget, QScrollBar)
    ]
    return sorted(c for c in constraints if c[1:] != (0, 0, unlimited, unlimited))


def _settle(qtbot: QtBot, wait_ms: int = 0) -> None:
    """Let the event loop run: a size change is applied over a few turns, and big windows build views on timers."""
    for _ in range(5):
        QApplication.processEvents()
    qtbot.wait(wait_ms)


def _open_at(qtbot: QtBot, build: Callable[[], QWidget], size: int, wait_ms: int = 0) -> QWidget:
    apply_text_size(size)
    window = build()
    qtbot.addWidget(window)
    window.show()
    _settle(qtbot, wait_ms)
    return window


@pytest.mark.usefixtures("restore_app_appearance")
@pytest.mark.parametrize("sizes", [(SMALL, LARGE), (LARGE, SMALL)], ids=["grow", "shrink"])
@pytest.mark.parametrize("surface", SURFACES, ids=lambda surface: surface.__name__)
def test_dialog_follows_a_text_size_change(qtbot: QtBot, surface: type[QWidget], sizes: tuple[int, int]) -> None:
    """Text stays whole and no size set at construction lingers from the old text size."""
    start, end = sizes
    apply_theme("dark")
    window = _open_at(qtbot, SURFACES[surface], start)
    apply_text_size(end)
    _settle(qtbot)
    assert clipped_text_on_every_page(window) == []
    changed = _size_constraints(window)
    window.close()

    assert changed == _size_constraints(_open_at(qtbot, SURFACES[surface], end))


@pytest.mark.usefixtures("restore_app_appearance")
def test_dialog_resizes_with_the_text(qtbot: QtBot) -> None:
    """An open dialog refits to larger and smaller text sizes without cutting off content."""
    apply_theme("dark")
    dialog = _open_at(qtbot, SettingsDialog, SMALL)
    small = dialog.size()
    apply_text_size(LARGE)
    _settle(qtbot)
    large = dialog.size()
    assert large != small
    assert clipped_text_on_every_page(dialog) == []
    apply_text_size(SMALL)
    _settle(qtbot)
    assert dialog.size() != large
    assert clipped_text_on_every_page(dialog) == []


@pytest.mark.usefixtures("restore_app_appearance")
def test_windows_follow_a_text_size_change(qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager) -> None:
    """The main window and the debug window match a fresh one after the text size grows under them."""
    apply_theme("dark")
    builders: dict[str, Callable[[], QWidget]] = {
        "main": lambda: MainWindow(make_shortcut_manager(), render_catalog_manager),
        "debug": DebugWindow,
    }
    for name, build in builders.items():
        window = _open_at(qtbot, build, SMALL, 200)
        apply_text_size(LARGE)
        _settle(qtbot, 200)
        changed = _size_constraints(window)
        window.close()
        assert changed == _size_constraints(_open_at(qtbot, build, LARGE, 200)), name


@pytest.mark.usefixtures("restore_app_appearance")
def test_playback_readouts_follow_a_text_size_change(qtbot: QtBot) -> None:
    """The frame field and the time are sized for the video's longest values at the new text size."""

    def playing_panel() -> QWidget:
        panel = SeekableVideoControlPanel()
        panel.show_video(total_frames=90000, frame_rate=25.0)
        return panel

    panel = _open_at(qtbot, playing_panel, SMALL)
    apply_text_size(LARGE)
    _settle(qtbot)
    changed = _size_constraints(panel)

    assert changed == _size_constraints(_open_at(qtbot, playing_panel, LARGE))
