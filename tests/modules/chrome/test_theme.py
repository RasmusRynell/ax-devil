"""Rendered regressions for light/dark text, surfaces and theme switching."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.theme import StatusColor, apply_theme, setup_theme
from ax_devil.modules.data_sources.scene_history import FrameEvent, ObjectHistory, SceneHistory
from ax_devil.modules.scene.inspection import build_entity_hover_html
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.settings_dialog import SettingsDialog
from ax_devil.modules.video_player.ui.controls import video_control_icon
from ax_devil.modules.video_player.ui.entity_hover_card import EntityHoverCard
from ax_devil.modules.video_viewer.media_tools.entity_list_widget import EntityListWidget
from ax_devil.modules.video_viewer.media_tools.event_log_widget import EventLogWidget
from ax_devil.modules.video_viewer.media_tools.object_history_widget import ObjectCard
from ax_devil.modules.workspace.content_browser import ContentBrowserWidget


def test_open_widgets_remain_readable_through_theme_switches(tmp_path: Path) -> None:
    """Keep theme-engine and QApplication lifetime isolated from the rest of the Qt suite."""
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path)],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def _foreground_pixels(image: QImage, rect: QRect, *, dark: bool) -> int:
    count = 0
    for y in range(rect.top(), min(image.height(), rect.bottom() + 1)):
        for x in range(rect.left(), min(image.width(), rect.right() + 1)):
            color = image.pixelColor(x, y)
            gray = (color.red() + color.green() + color.blue()) / 3
            if (dark and gray > 150) or (not dark and gray < 100):
                count += 1
    return count


def _luminance(color: QColor) -> float:
    channels = [value / 255 for value in (color.red(), color.green(), color.blue())]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _verify_theme_rendering(cache_dir: Path) -> None:
    # The theme engine's SVG cache is unrelated to app storage; isolate it too.
    from unittest.mock import patch

    with patch("qdarktheme._template.filter.get_cash_root_path", return_value=cache_dir):
        from ax_devil.modules.settings.config_manager import ConfigManager

        ConfigManager().set_config_path(cache_dir / "config.json")
        app = QApplication([])
        setup_theme(app, "dark")
        settings = GlobalSettings()
        settings.theme_changed.connect(apply_theme)
        dialog = SettingsDialog()
        frame = FrameIdentifier(5, 5_000_000)
        entity = Entity(
            id=EntityId("person-1"),
            observations=[
                Observation(
                    geometry=BoundingBox.from_xywh(0.1, 0.2, 0.3, 0.4),
                    classification=[Classification("human", Score(0.9))],
                    frame_number=5,
                )
            ],
        )
        scene = Scene(time_slice=TimeSlice(0, 0))
        scene.add_entity(entity)
        history = SceneHistory(
            events=(FrameEvent(frame, "Delete", "Delete person-1", ("person-1",)),),
            objects=(ObjectHistory("person-1", ("human",), ((0, 8),), tuple(i * 1_000_000 for i in range(10))),),
            frame_count=10,
        )
        entities = EntityListWidget()
        entities.resize(400, 300)
        events = EventLogWidget(history)
        events.resize(500, 150)
        card = ObjectCard("person-1", history)
        card.resize(400, 300)
        host = QWidget()
        host.resize(500, 350)
        hover = EntityHoverCard(host)
        browser = ContentBrowserWidget()
        browser.resize(300, 150)
        for widget in (entities, events, card, host, browser):
            widget.show()
        entities.update_scene(scene, frame, {})
        entities._on_index_clicked(entities._model.index(0, 0))
        events.update_scene(None, frame, {})
        card.show_frame(scene, 5)
        card._button.setChecked(True)
        hover.show_for("person-1", build_entity_hover_html(entity), 20, 20)

        for mode in ("light", "dark", "light"):
            dialog._theme_combo.setCurrentIndex(dialog._theme_combo.findData(mode))
            dialog._on_apply()
            app.processEvents()
            app.processEvents()
            dark = mode == "dark"
            expected_surface = "#191a1b" if dark else "#f1f5f9"
            assert browser.palette().color(QPalette.ColorRole.Window).name() == expected_surface
            assert card.palette().color(QPalette.ColorRole.Window).name() == expected_surface
            assert app.palette().color(QPalette.ColorRole.Mid).name() == ("#252627" if dark else "#d5dee8")
            viewport = entities._list_view.viewport()
            image = viewport.grab().toImage()
            assert _foreground_pixels(image, QRect(25, 3, 160, 18), dark=dark) > 20, mode
            assert _foreground_pixels(image, QRect(10, 30, 300, 110), dark=dark) > 100, mode
            events_image = events._list_view.viewport().grab().toImage()
            assert _foreground_pixels(events_image, QRect(170, 3, 200, 18), dark=dark) > 20, mode
            hover_image = hover.grab().toImage()
            assert _foreground_pixels(hover_image, QRect(10, 8, 150, 70), dark=dark) > 100, mode
            detail_image = card._details.grab().toImage()
            assert _foreground_pixels(detail_image, detail_image.rect(), dark=dark) > 100, mode

            # Selection must preserve readable summaries and cached rich text.
            entities._list_view.setCurrentIndex(entities._model.index(0, 0))
            app.processEvents()
            selected = viewport.grab().toImage()
            assert _foreground_pixels(selected, QRect(25, 3, 160, 18), dark=dark) > 20, mode
            assert _foreground_pixels(selected, QRect(10, 30, 300, 110), dark=dark) > 100, mode
            entities._list_view.clearSelection()

            icon = video_control_icon(host, host.style().StandardPixmap.SP_MediaPlay).pixmap(32, 32).toImage()
            opaque = [
                icon.pixelColor(x, y) for y in range(32) for x in range(32) if icon.pixelColor(x, y).alpha() > 200
            ]
            assert opaque and all(color.red() == color.green() == color.blue() == 255 for color in opaque)
            for status in StatusColor:
                foreground = _luminance(status.color(app.palette()))
                background = _luminance(app.palette().color(QPalette.ColorRole.AlternateBase))
                assert (max(foreground, background) + 0.05) / (min(foreground, background) + 0.05) >= 4.5

        # Qt CSS alpha uses 0..255. A fractional value would render this nearly invisible.
        background_widget = QWidget()
        background_widget.resize(30, 30)
        background_widget.setStyleSheet("background-color: white;")
        label = QLabel(background_widget)
        label.setGeometry(0, 0, 30, 30)
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Text, QColor("#000000"))
        color_css = palette_color_css(palette, QPalette.ColorRole.Text, alpha=0.5)
        label.setStyleSheet(f"background-color: {color_css};")
        background_widget.show()
        app.processEvents()
        image = background_widget.grab().toImage()
        assert 126 <= image.pixelColor(15, 15).red() <= 129
        for widget in (entities, events, card, host, browser, background_widget, dialog):
            widget.close()


if __name__ == "__main__":
    _verify_theme_rendering(Path(sys.argv[1]))
