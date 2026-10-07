"""Save offscreen screenshots of the main UI surfaces, for checking a UI change before and after.

Runs inside the test suite's isolated home folder, so it never reads or changes the user's settings, and never opens a
window on the desktop. Text sizes switch live in one window per theme, as the Settings dialog does::

    PYTHONPATH=. uv run pytest -p tests.conftest tools/ui_screenshots.py -q
    PYTHONPATH=. AX_DEVIL_UI_SHOTS=/tmp/after uv run pytest -p tests.conftest tools/ui_screenshots.py -q

Shots go to ``AX_DEVIL_UI_SHOTS`` (default ``/tmp/ax-devil-ui-shots``) as ``<theme>-<text size>-<surface>.png``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.cache.cache_manager import CacheManager
from ax_devil.modules.chrome.theme import apply_text_size, apply_theme
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.quick_setup_dialog import QuickSetupDialog
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.settings_dialog import SettingsDialog
from ax_devil.modules.settings.text_size import TextSize
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from ax_devil.modules.workspace import VideoFileStartup

OUT = Path(os.environ.get("AX_DEVIL_UI_SHOTS", "/tmp/ax-devil-ui-shots"))
WINDOW_SIZES = {"wide": (1440, 900), "narrow": (1000, 640)}
TEXT_SIZES = (TextSize.MEDIUM, TextSize.LARGER)


def _grab_next_dialog(name: str, before: Callable[[QDialog], None] | None = None) -> None:
    """Save and close the dialog that the next call opens modally, after running *before* on it."""

    def grab() -> None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                if before is not None:
                    before(widget)
                    QApplication.processEvents()
                widget.grab().save(str(OUT / f"{name}.png"))
                natural = widget.size()
                for size_name, (width, height) in WINDOW_SIZES.items():
                    widget.resize(min(natural.width(), width), min(natural.height(), height))
                    QApplication.processEvents()
                    widget.grab().save(str(OUT / f"{name}-{size_name}.png"))
                widget.reject()

    QTimer.singleShot(300, grab)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_screenshots(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
    video_file_factory: Callable[[float, int], Path],
    theme: str,
) -> None:
    """Save the welcome screen, a video with media tools, and the main dialogs, switching text size live."""
    OUT.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(CacheManager, "_get_base_cache_dir", lambda self: tmp_path / "cache")
    apply_theme(theme)
    # Change the text size through the setting, as the app does, so dialogs show the matching choice.
    GlobalSettings.reset_instance()
    settings = GlobalSettings()
    settings.text_size_changed.connect(lambda size: apply_text_size(size.body_px))
    apply_text_size(settings.text_size.body_px)

    shortcuts = ShortcutManager()
    shortcuts.register_defaults()
    window = MainWindow(shortcuts, render_catalog_manager, use_custom_frame=True)
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(100)  # Let the window restore its remembered size before choosing ours.
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        window.resize(*WINDOW_SIZES["wide"])
        qtbot.wait(200)
        window.grab().save(str(OUT / f"{theme}-{text_size.value}-welcome.png"))

    session = window._workspace_session
    session.load_startup_content(VideoFileStartup(video_path=video_file_factory(2.0, 30)))
    viewer = session.focused_offline_viewer()
    assert viewer is not None
    viewer.pause_playback()
    viewport = viewer.findChild(FrameViewport)
    assert viewport is not None
    qtbot.waitUntil(lambda: viewport._video_frame is not None, timeout=10000)
    shortcuts.get_action("view.toggle_media_tools").trigger()
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        prefix = f"{theme}-{text_size.value}"
        for size_name, size in WINDOW_SIZES.items():
            window.resize(*size)
            qtbot.wait(500)
            window.grab().save(str(OUT / f"{prefix}-video-{size_name}.png"))
        for name, action_id in (
            ("settings", "app.settings"),
            ("shortcuts", "app.keyboard_shortcuts"),
            ("add-video", "app.add_video"),
        ):
            _grab_next_dialog(f"{prefix}-{name}")
            shortcuts.get_action(action_id).trigger()
        _grab_next_dialog(
            f"{prefix}-settings-storage", lambda dialog: cast(SettingsDialog, dialog)._tabs.setCurrentIndex(2)
        )
        shortcuts.get_action("app.settings").trigger()
        _grab_next_dialog(
            f"{prefix}-settings-streams", lambda dialog: cast(SettingsDialog, dialog)._tabs.setCurrentIndex(1)
        )
        shortcuts.get_action("app.settings").trigger()
        _grab_next_dialog(
            f"{prefix}-settings-manual-cache",
            lambda dialog: cast(SettingsDialog, dialog)._video_cache_mode.setCurrentIndex(1),
        )
        shortcuts.get_action("app.settings").trigger()
        _grab_next_dialog(f"{prefix}-quick-setup")
        window.show_quick_setup()
    # Quick Setup opens with room for the largest text size; picking it must not need scrolling.
    settings.text_size = TextSize.SMALL
    _grab_next_dialog(
        f"{theme}-quick-setup-small-to-larger",
        lambda dialog: cast(QuickSetupDialog, dialog).text_size_row.select(TextSize.LARGER),
    )
    window.show_quick_setup()
    session.clear()
