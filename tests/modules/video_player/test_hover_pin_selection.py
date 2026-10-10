"""Click-to-pin hover selection behavior for the video renderer."""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QTextBrowser
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.inspection import debug_section_html
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.video_player.engine.data_types import (
    HoverHit,
    HoverInteractionProvider,
    VideoFrame,
    VideoFrameWithOverlays,
    VideoOverlayData,
)
from ax_devil.modules.video_player.engine.quick.preparation import PreparedDrawing
from ax_devil.modules.video_player.engine.renderer import VideoFrameRenderer
from ax_devil.modules.video_player.ui.entity_hover_card import EntityHoverCard
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from tests.helpers.qt_events import make_wheel_event


class _MovingProvider:
    def __init__(self) -> None:
        self.bounds = (0.10, 0.10, 0.20, 0.20)
        self.card_html = "<span>entity-1 frame-1</span>"

    def hit_test(self, nx: float, ny: float) -> HoverHit | None:
        x, y, w, h = self.bounds
        if x <= nx <= x + w and y <= ny <= y + h:
            return HoverHit(target_id="entity-1", bounds=self.bounds, card_html=self.card_html)
        return None

    def get_hit_by_id(self, target_id: str) -> HoverHit | None:
        if target_id != "entity-1":
            return None
        return HoverHit(target_id="entity-1", bounds=self.bounds, card_html=self.card_html)


class _ToggleProvider:
    def __init__(self) -> None:
        self.active = True

    def hit_test(self, nx: float, ny: float) -> HoverHit | None:
        if not self.active:
            return None
        if 0.10 <= nx <= 0.30 and 0.10 <= ny <= 0.30:
            return HoverHit(
                target_id="entity-1",
                bounds=(0.10, 0.10, 0.20, 0.20),
                card_html="<span>entity-1</span>",
            )
        return None

    def get_hit_by_id(self, target_id: str) -> HoverHit | None:
        if not self.active or target_id != "entity-1":
            return None
        return HoverHit(
            target_id="entity-1",
            bounds=(0.10, 0.10, 0.20, 0.20),
            card_html="<span>entity-1</span>",
        )


def _make_display_data(provider: HoverInteractionProvider) -> VideoFrameWithOverlays:
    image = QImage(640, 480, QImage.Format.Format_RGB32)
    image.fill(0)
    frame = VideoFrame(image=image, timestamp=0.0)
    overlays = VideoOverlayData(
        drawing_generator=lambda context, _settings: PreparedDrawing(),
        timestamp=0.0,
        interaction_provider=provider,
    )
    return VideoFrameWithOverlays(
        frame=frame,
        overlays=overlays,
    )


def _make_renderer(qtbot: QtBot, provider: HoverInteractionProvider | None = None) -> VideoFrameRenderer:
    renderer = VideoFrameRenderer()
    renderer.resize(640, 480)
    renderer.show()
    qtbot.addWidget(renderer)
    renderer.display_frame(_make_display_data(provider or _MovingProvider()))
    return renderer


def _mouse_move(renderer: VideoFrameRenderer, pos: QPoint) -> None:
    # Send the event directly: Wayland may prohibit QTest's native cursor warping.
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(pos),
        QPointF(renderer.mapToGlobal(pos)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(renderer, event)


def _mouse_click(qtbot: QtBot, renderer: VideoFrameRenderer, pos: QPoint) -> None:
    cast(Any, qtbot).mouseClick(renderer, Qt.MouseButton.LeftButton, pos=pos)


def _card(renderer: VideoFrameRenderer) -> EntityHoverCard:
    card = renderer.findChild(EntityHoverCard)
    assert card is not None
    return card


def _browser(renderer: VideoFrameRenderer) -> QTextBrowser:
    browser = _card(renderer).findChild(QTextBrowser)
    assert browser is not None
    return browser


def _is_pinned(renderer: VideoFrameRenderer) -> bool:
    """A pinned card stays shown and takes the mouse; a hover card lets it pass to the video."""
    card = _card(renderer)
    return card.isVisible() and not card.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_click_pins_selection_and_empty_click_clears(qtbot: QtBot) -> None:
    renderer = _make_renderer(qtbot)

    _mouse_move(renderer, QPoint(100, 80))
    assert _card(renderer).isVisible()
    assert not _is_pinned(renderer)
    _mouse_click(qtbot, renderer, QPoint(100, 80))

    assert _is_pinned(renderer)
    assert _browser(renderer).toPlainText() == "entity-1 frame-1"

    # Move away from target; pinned selection should remain visible.
    _mouse_move(renderer, QPoint(500, 420))
    assert _is_pinned(renderer)
    assert _browser(renderer).toPlainText() == "entity-1 frame-1"

    # Click inside video but not on a target to clear selection.
    _mouse_click(qtbot, renderer, QPoint(500, 420))
    assert not _card(renderer).isVisible()
    assert not _is_pinned(renderer)


def _make_pinned_renderer(qtbot: QtBot, size: tuple[int, int]) -> VideoFrameRenderer:
    renderer = VideoFrameRenderer()
    renderer.resize(*size)
    qtbot.addWidget(renderer)
    renderer.show()
    provider = _MovingProvider()
    provider.card_html = "".join(debug_section_html({"debug": {f"metric_{index}": index for index in range(100)}}))
    renderer.display_frame(_make_display_data(provider))
    target = renderer.frame_display_rect()
    assert target is not None
    object_point = target.topLeft() + QPointF(0.2 * target.width(), 0.2 * target.height())
    _mouse_click(qtbot, renderer, object_point.toPoint())
    return renderer


def test_pinned_inspector_takes_wheel_and_clicks_without_changing_the_video(qtbot: QtBot) -> None:
    renderer = _make_pinned_renderer(qtbot, (640, 480))
    card = _card(renderer)
    browser = _browser(renderer)
    scroll = browser.verticalScrollBar()
    assert scroll.maximum() > 0

    event = make_wheel_event(QPointF(20, 20), -120)
    QApplication.sendEvent(browser.viewport(), event)
    assert scroll.value() > 0
    scroll.setValue(scroll.maximum())
    at_end = make_wheel_event(QPointF(20, 20), -120)
    QApplication.sendEvent(browser.viewport(), at_end)
    assert renderer.viewport_state.zoom_level == renderer.viewport_state.zoom_min

    window = renderer.windowHandle()
    assert window is not None
    cast(Any, qtbot).mouseClick(window, Qt.MouseButton.LeftButton, pos=card.pos() + QPoint(2, 2))
    assert _is_pinned(renderer)


def test_paused_pinned_inspector_refits_on_viewer_resize(qtbot: QtBot) -> None:
    renderer = _make_pinned_renderer(qtbot, (1200, 700))
    browser = _browser(renderer)
    cursor = browser.document().find("metric_10")
    assert cursor.hasSelection()
    browser.setTextCursor(cursor)

    renderer.resize(320, 300)
    QApplication.processEvents()

    assert _is_pinned(renderer)
    assert renderer.rect().contains(_card(renderer).geometry())
    assert browser.textCursor().selectedText() == "metric_10"


def test_hover_and_click_share_object_anchored_card_position(qtbot: QtBot) -> None:
    renderer = _make_renderer(qtbot)

    _mouse_move(renderer, QPoint(110, 90))

    hover_pos = _card(renderer).pos()
    assert _card(renderer).isVisible()

    _mouse_click(qtbot, renderer, QPoint(110, 90))

    assert _is_pinned(renderer)
    assert _card(renderer).pos() == hover_pos


def test_pinned_selection_tracks_target_across_frames(qtbot: QtBot) -> None:
    provider = _MovingProvider()
    renderer = _make_renderer(qtbot, provider)

    _mouse_move(renderer, QPoint(100, 80))
    _mouse_click(qtbot, renderer, QPoint(100, 80))

    assert _browser(renderer).toPlainText() == "entity-1 frame-1"
    old_pos = _card(renderer).pos()

    provider.bounds = (0.60, 0.50, 0.20, 0.20)
    provider.card_html = "<span>entity-1 frame-2</span>"
    renderer.display_frame(_make_display_data(provider))

    assert _is_pinned(renderer)
    assert _browser(renderer).toPlainText() == "entity-1 frame-2"
    assert _card(renderer).pos() != old_pos


def test_refresh_last_frame_clears_pinned_selection_when_target_disappears(qtbot: QtBot) -> None:
    provider = _ToggleProvider()
    renderer = _make_renderer(qtbot, provider)

    _mouse_move(renderer, QPoint(100, 80))
    _mouse_click(qtbot, renderer, QPoint(100, 80))

    assert _is_pinned(renderer)

    provider.active = False
    renderer.refresh_last_frame()

    assert not _card(renderer).isVisible()
    assert not _is_pinned(renderer)


def test_pinned_selection_repositions_card_when_zoom_changes_viewport(qtbot: QtBot) -> None:
    provider = _MovingProvider()
    renderer = _make_renderer(qtbot, provider)

    _mouse_move(renderer, QPoint(100, 80))
    _mouse_click(qtbot, renderer, QPoint(100, 80))

    old_pos = _card(renderer).pos()

    renderer.wheelEvent(make_wheel_event(QPointF(100.0, 80.0), 120))

    assert _is_pinned(renderer)
    assert _card(renderer).pos() != old_pos


def test_disabling_object_interaction_clears_pinned_card_and_highlight(qtbot: QtBot) -> None:
    renderer = FrameViewport()
    renderer.resize(640, 480)
    renderer.show()
    qtbot.addWidget(renderer)
    renderer.display_frame(_make_display_data(_MovingProvider()))
    _mouse_move(renderer, QPoint(100, 80))
    _mouse_click(qtbot, renderer, QPoint(100, 80))
    assert _is_pinned(renderer)

    settings = GlobalSettings()
    try:
        settings.set_overlay_enabled(OverlayPreference.HOVER, False)
        assert not _card(renderer).isVisible()
        # The highlight has no public observation short of reading pixels; a stale one would stay drawn.
        assert renderer._hover_highlight_overlay is None
        _mouse_move(renderer, QPoint(100, 80))
        _mouse_click(qtbot, renderer, QPoint(100, 80))
        assert not _card(renderer).isVisible()
        settings.set_overlay_enabled(OverlayPreference.HOVER, True)
        _mouse_move(renderer, QPoint(100, 80))
        assert _card(renderer).isVisible()
    finally:
        settings.set_overlay_enabled(OverlayPreference.HOVER, True)
