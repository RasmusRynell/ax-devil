"""Click-to-pin hover selection behavior for the video renderer."""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication
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
from ax_devil.modules.video_player.ui.viewport import FrameViewport


class _StaticProvider:
    def hit_test(self, nx: float, ny: float) -> HoverHit | None:
        if 0.10 <= nx <= 0.30 and 0.10 <= ny <= 0.30:
            return HoverHit(
                target_id="entity-1",
                bounds=(0.10, 0.10, 0.20, 0.20),
                card_html="<span>entity-1</span>",
            )
        return None

    def get_hit_by_id(self, target_id: str) -> HoverHit | None:
        if target_id != "entity-1":
            return None
        return HoverHit(
            target_id="entity-1",
            bounds=(0.10, 0.10, 0.20, 0.20),
            card_html="<span>entity-1</span>",
        )


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


def _make_renderer(qtbot: QtBot) -> VideoFrameRenderer:
    renderer = VideoFrameRenderer()
    renderer.resize(640, 480)
    renderer.show()
    qtbot.addWidget(renderer)

    image = QImage(640, 480, QImage.Format.Format_RGB32)
    image.fill(0)

    frame = VideoFrame(image=image, timestamp=0.0)
    overlays = VideoOverlayData(
        drawing_generator=lambda context, _settings: PreparedDrawing(),
        timestamp=0.0,
        interaction_provider=_StaticProvider(),
    )
    renderer.display_frame(
        VideoFrameWithOverlays(
            frame=frame,
            overlays=overlays,
        )
    )
    return renderer


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


def _make_wheel_event(position: QPointF, delta_y: int) -> QWheelEvent:
    local_pos = QPointF(position)
    global_pos = QPointF(position)
    pixel_delta = QPoint(0, 0)
    angle_delta = QPoint(0, delta_y)
    return QWheelEvent(
        local_pos,
        global_pos,
        pixel_delta,
        angle_delta,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


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


def test_click_pins_selection_and_empty_click_clears(qtbot: QtBot) -> None:
    renderer = _make_renderer(qtbot)

    _mouse_move(renderer, QPoint(100, 80))
    assert renderer._hover_card.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    _mouse_click(qtbot, renderer, QPoint(100, 80))

    assert renderer._hover_card.isVisible()
    assert not renderer._hover_card.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert renderer._hover_hit is not None
    assert renderer._hover_hit.target_id == "entity-1"

    # Move away from target; pinned selection should remain visible.
    _mouse_move(renderer, QPoint(500, 420))
    assert renderer._hover_card.isVisible()
    assert renderer._hover_hit is not None
    assert renderer._hover_hit.target_id == "entity-1"

    # Click inside video but not on a target to clear selection.
    _mouse_click(qtbot, renderer, QPoint(500, 420))
    assert not renderer._hover_card.isVisible()
    assert renderer._hover_hit is None
    assert renderer._hover_card.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def _make_pinned_renderer(qtbot: QtBot, size: tuple[int, int]) -> VideoFrameRenderer:
    renderer = VideoFrameRenderer()
    renderer.resize(*size)
    qtbot.addWidget(renderer)
    renderer.show()
    provider = _MovingProvider()
    provider.card_html = "".join(debug_section_html({"debug": {f"metric_{index}": index for index in range(100)}}))
    renderer.display_frame(_make_display_data(provider))
    target = renderer._hover_target_rect()
    assert target is not None
    object_point = target.topLeft() + QPointF(0.2 * target.width(), 0.2 * target.height())
    _mouse_click(qtbot, renderer, object_point.toPoint())
    return renderer


def test_pinned_inspector_takes_wheel_and_clicks_without_changing_the_video(qtbot: QtBot) -> None:
    renderer = _make_pinned_renderer(qtbot, (640, 480))
    card = renderer._hover_card
    scroll = card._browser.verticalScrollBar()
    old_bounds = renderer._hover_target_rect()
    assert scroll.maximum() > 0

    event = _make_wheel_event(QPointF(20, 20), -120)
    QApplication.sendEvent(card._browser.viewport(), event)
    assert scroll.value() > 0
    scroll.setValue(scroll.maximum())
    at_end = _make_wheel_event(QPointF(20, 20), -120)
    QApplication.sendEvent(card._browser.viewport(), at_end)
    assert renderer._hover_target_rect() == old_bounds

    window = renderer.windowHandle()
    assert window is not None
    cast(Any, qtbot).mouseClick(window, Qt.MouseButton.LeftButton, pos=card.pos() + QPoint(2, 2))
    assert renderer._pinned_target_id == "entity-1"
    assert card.isVisible()


def test_paused_pinned_inspector_refits_on_viewer_resize(qtbot: QtBot) -> None:
    renderer = _make_pinned_renderer(qtbot, (1200, 700))

    renderer.resize(320, 300)
    QApplication.processEvents()

    assert renderer._pinned_target_id == "entity-1"
    assert renderer.rect().contains(renderer._hover_card.geometry())


def test_hover_and_click_share_object_anchored_card_position(qtbot: QtBot) -> None:
    renderer = _make_renderer(qtbot)

    _mouse_move(renderer, QPoint(110, 90))

    hover_pos = renderer._hover_card.pos()
    assert renderer._hover_card.isVisible()

    _mouse_click(qtbot, renderer, QPoint(110, 90))

    assert renderer._pinned_target_id is not None
    assert renderer._hover_card.pos() == hover_pos


def test_pinned_selection_tracks_target_across_frames(qtbot: QtBot) -> None:
    renderer = VideoFrameRenderer()
    renderer.resize(640, 480)
    renderer.show()
    qtbot.addWidget(renderer)

    provider = _MovingProvider()
    renderer.display_frame(_make_display_data(provider))

    _mouse_move(renderer, QPoint(100, 80))
    _mouse_click(qtbot, renderer, QPoint(100, 80))

    assert renderer._hover_hit is not None
    assert renderer._hover_hit.bounds == (0.10, 0.10, 0.20, 0.20)
    old_pos = renderer._hover_card.pos()

    provider.bounds = (0.60, 0.50, 0.20, 0.20)
    provider.card_html = "<span>entity-1 frame-2</span>"
    renderer.display_frame(_make_display_data(provider))

    assert renderer._hover_hit is not None
    assert renderer._hover_hit.bounds == (0.60, 0.50, 0.20, 0.20)
    assert renderer._hover_card._browser.toPlainText() == "entity-1 frame-2"
    assert renderer._hover_card.pos() != old_pos


def test_refresh_last_frame_clears_pinned_selection_when_target_disappears(qtbot: QtBot) -> None:
    renderer = VideoFrameRenderer()
    renderer.resize(640, 480)
    renderer.show()
    qtbot.addWidget(renderer)

    provider = _ToggleProvider()
    renderer.display_frame(_make_display_data(provider))

    _mouse_move(renderer, QPoint(100, 80))
    _mouse_click(qtbot, renderer, QPoint(100, 80))

    assert renderer._pinned_target_id is not None
    assert renderer._hover_card.isVisible()

    provider.active = False
    renderer.refresh_last_frame()

    assert renderer._pinned_target_id is None
    assert renderer._hover_hit is None
    assert not renderer._hover_card.isVisible()


def test_pinned_selection_repositions_card_when_zoom_changes_viewport(qtbot: QtBot) -> None:
    renderer = VideoFrameRenderer()
    renderer.resize(640, 480)
    renderer.show()
    qtbot.addWidget(renderer)

    provider = _MovingProvider()
    renderer.display_frame(_make_display_data(provider))

    _mouse_move(renderer, QPoint(100, 80))
    _mouse_click(qtbot, renderer, QPoint(100, 80))

    old_pos = renderer._hover_card.pos()

    renderer.wheelEvent(_make_wheel_event(QPointF(100.0, 80.0), 120))

    assert renderer._pinned_target_id is not None
    assert renderer._hover_card.isVisible()
    assert renderer._hover_card.pos() != old_pos


def test_disabling_object_interaction_clears_pinned_card_and_highlight(qtbot: QtBot) -> None:
    renderer = FrameViewport()
    renderer.resize(640, 480)
    renderer.show()
    qtbot.addWidget(renderer)
    renderer.display_frame(_make_display_data(_MovingProvider()))
    _mouse_move(renderer, QPoint(100, 80))
    _mouse_click(qtbot, renderer, QPoint(100, 80))
    assert renderer._hover_card.isVisible()

    settings = GlobalSettings()
    try:
        settings.set_overlay_enabled(OverlayPreference.HOVER, False)
        assert renderer._pinned_target_id is None
        assert renderer._hover_highlight_overlay is None
        assert not renderer._hover_card.isVisible()
        _mouse_move(renderer, QPoint(100, 80))
        _mouse_click(qtbot, renderer, QPoint(100, 80))
        assert not renderer._hover_card.isVisible()
        settings.set_overlay_enabled(OverlayPreference.HOVER, True)
        _mouse_move(renderer, QPoint(100, 80))
        assert renderer._hover_card.isVisible()
    finally:
        settings.set_overlay_enabled(OverlayPreference.HOVER, True)
