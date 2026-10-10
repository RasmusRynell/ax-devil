"""Rendered regressions for light/dark text, surfaces and theme switching."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QListView, QTabWidget, QToolButton, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.application_shell.settings_dialog import SettingsDialog
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.theme import StatusColor, apply_text_size, apply_theme
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
from ax_devil.modules.video_player.ui.controls import video_control_icon
from ax_devil.modules.video_player.ui.entity_hover_card import EntityHoverCard
from ax_devil.modules.video_viewer.media_tools.entity_list_widget import EntityListWidget
from ax_devil.modules.video_viewer.media_tools.event_log_widget import EventLogWidget
from ax_devil.modules.video_viewer.media_tools.object_history_widget import ObjectCard
from ax_devil.modules.workspace.content_browser import ContentBrowserWidget


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


def _list_view(widget: QWidget) -> QListView:
    view = widget.findChild(QListView)
    assert view is not None
    return view


@pytest.mark.usefixtures("restore_app_appearance")
def test_open_views_stay_readable_through_theme_switches(qtbot: QtBot, qapp: QApplication) -> None:
    """Text in already-open inspection views, surfaces and status colors follow each switch between themes."""
    apply_theme("dark")
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
        qtbot.addWidget(widget)
        widget.show()
    entities.update_scene(scene, frame, {})
    entity_view = _list_view(entities)
    first_row = entity_view.model().index(0, 0)
    QTest.mouseClick(entity_view.viewport(), Qt.MouseButton.LeftButton, pos=entity_view.visualRect(first_row).center())
    events.update_scene(None, frame, {})
    card.show_frame(scene, 5)
    object_button = card.findChild(QToolButton, "objectButton")
    assert object_button is not None
    object_button.setChecked(True)
    hover.show_for("person-1", build_entity_hover_html(entity), 20, 20)

    for mode in ("light", "dark", "light"):
        apply_theme(mode)
        QApplication.processEvents()
        dark = mode == "dark"
        # Custom-painted surfaces and borders use the theme's palette, not the platform's defaults.
        for color in (
            browser.palette().color(QPalette.ColorRole.Window),
            card.palette().color(QPalette.ColorRole.Window),
            qapp.palette().color(QPalette.ColorRole.Mid),
        ):
            assert (color.lightnessF() < 0.5) is dark, mode
        image = entity_view.viewport().grab().toImage()
        assert _foreground_pixels(image, QRect(25, 3, 160, 18), dark=dark) > 20, mode
        assert _foreground_pixels(image, QRect(10, 30, 300, 110), dark=dark) > 100, mode
        events_image = _list_view(events).viewport().grab().toImage()
        assert _foreground_pixels(events_image, QRect(170, 3, 200, 18), dark=dark) > 20, mode
        hover_image = hover.grab().toImage()
        assert _foreground_pixels(hover_image, QRect(10, 8, 150, 70), dark=dark) > 100, mode
        detail_image = card._details.grab().toImage()
        assert _foreground_pixels(detail_image, detail_image.rect(), dark=dark) > 100, mode

        # Selection must preserve readable summaries and cached rich text.
        entity_view.setCurrentIndex(first_row)
        QApplication.processEvents()
        selected = entity_view.viewport().grab().toImage()
        assert _foreground_pixels(selected, QRect(25, 3, 160, 18), dark=dark) > 20, mode
        assert _foreground_pixels(selected, QRect(10, 30, 300, 110), dark=dark) > 100, mode
        entity_view.clearSelection()

        # Playback controls sit on the near-black video in both themes, so they stay light.
        icon = video_control_icon(Icon.PLAY).pixmap(32, 32).toImage()
        opaque = [icon.pixelColor(x, y) for y in range(32) for x in range(32) if icon.pixelColor(x, y).alpha() > 200]
        assert opaque and all(color.lightnessF() > 0.9 for color in opaque), mode
        background = _luminance(qapp.palette().color(QPalette.ColorRole.AlternateBase))
        for status in StatusColor:
            foreground = _luminance(status.color(qapp.palette()))
            assert (max(foreground, background) + 0.05) / (min(foreground, background) + 0.05) >= 4.5, (mode, status)


@pytest.mark.usefixtures("restore_app_appearance")
def test_settings_fields_fit_their_text_through_theme_and_text_size_changes(qtbot: QtBot) -> None:
    """Open forms keep editors readable when text grows and shrinks, including spinbox and combo line edits."""
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    dialog._video_cache_mode.setCurrentIndex(1)  # Reveal the manual cache-size field.
    dialog.show()
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    for mode, size in (("dark", 13), ("light", 21), ("dark", 13)):
        apply_theme(mode)
        apply_text_size(size)
        for page in range(tabs.count()):
            tabs.setCurrentIndex(page)
            QApplication.processEvents()
            for field in dialog.findChildren(QLineEdit):
                if field.isVisible():
                    assert field.contentsRect().height() >= field.fontMetrics().height(), (mode, size)
                    parent = field.parentWidget()
                    assert parent is not None and parent.rect().contains(field.geometry()), (mode, size)


def test_translucent_palette_color_stays_visible(qtbot: QtBot) -> None:
    """Qt CSS alpha uses 0..255; a fractional value would render a half-transparent color nearly invisible."""
    background = QWidget()
    qtbot.addWidget(background)
    background.resize(30, 30)
    background.setStyleSheet("background-color: white;")
    label = QLabel(background)
    label.setGeometry(0, 0, 30, 30)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Text, QColor("#000000"))
    label.setStyleSheet(f"background-color: {palette_color_css(palette, QPalette.ColorRole.Text, alpha=0.5)};")
    background.show()
    QApplication.processEvents()
    assert 126 <= background.grab().toImage().pixelColor(15, 15).red() <= 129
