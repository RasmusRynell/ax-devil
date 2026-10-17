"""Hover and click selection in the video renderer: the chip follows the pointer, a click selects an object."""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.video_player.engine.data_types import (
    HoverHit,
    VideoFrame,
    VideoFrameWithOverlays,
    VideoOverlayData,
)
from ax_devil.modules.video_player.engine.quick.preparation import PreparedDrawing
from ax_devil.modules.video_player.engine.renderer import VideoFrameRenderer
from ax_devil.modules.video_player.ui.hover_chip import HoverChip
from ax_devil.modules.video_player.ui.viewport import FrameViewport

ON_OBJECT = QPoint(100, 80)
OFF_OBJECT = QPoint(500, 420)


class _Provider:
    """One object that can move or leave the frame."""

    def __init__(self) -> None:
        self.bounds: tuple[float, float, float, float] | None = (0.10, 0.10, 0.20, 0.20)
        self.detail = "conf 0.97"

    def _hit(self) -> HoverHit | None:
        if self.bounds is None:
            return None
        return HoverHit("entity-1", self.bounds, "entity-1 person", self.detail, "person")

    def hit_test(self, nx: float, ny: float) -> HoverHit | None:
        hit = self._hit()
        if hit is None:
            return None
        x, y, w, h = hit.bounds
        return hit if x <= nx <= x + w and y <= ny <= y + h else None

    def get_hit_by_id(self, target_id: str) -> HoverHit | None:
        return self._hit() if target_id == "entity-1" else None


def _frame(provider: _Provider) -> VideoFrameWithOverlays:
    image = QImage(640, 480, QImage.Format.Format_RGB32)
    image.fill(0)
    overlays = VideoOverlayData(
        drawing_generator=lambda context, _settings: PreparedDrawing(), timestamp=0.0, interaction_provider=provider
    )
    return VideoFrameWithOverlays(frame=VideoFrame(image=image, timestamp=0.0), overlays=overlays)


def _renderer(qtbot: QtBot, provider: _Provider) -> VideoFrameRenderer:
    renderer = VideoFrameRenderer()
    renderer.resize(640, 480)
    renderer.show()
    qtbot.addWidget(renderer)
    renderer.display_frame(_frame(provider))
    return renderer


def _move(renderer: VideoFrameRenderer, pos: QPoint) -> None:
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


def _click(qtbot: QtBot, renderer: VideoFrameRenderer, pos: QPoint) -> None:
    cast(Any, qtbot).mouseClick(renderer, Qt.MouseButton.LeftButton, pos=pos)


def _chip(renderer: VideoFrameRenderer) -> HoverChip:
    chip = renderer.findChild(HoverChip)
    assert chip is not None
    return chip


def _record(renderer: VideoFrameRenderer) -> list[str | None]:
    changes: list[str | None] = []
    renderer.selectionChanged.connect(changes.append)
    return changes


def test_clicking_an_object_selects_it_and_clicking_empty_video_clears(qtbot: QtBot) -> None:
    renderer = _renderer(qtbot, _Provider())
    changes = _record(renderer)

    _click(qtbot, renderer, ON_OBJECT)
    _click(qtbot, renderer, ON_OBJECT)  # Selecting what is selected says nothing new.
    assert changes == ["entity-1"]
    assert renderer._selected_bounds == (0.10, 0.10, 0.20, 0.20)

    _click(qtbot, renderer, OFF_OBJECT)
    assert changes == ["entity-1", None]
    assert renderer._selected_bounds is None


def test_selected_object_that_leaves_the_frame_keeps_its_id_and_loses_its_box(qtbot: QtBot) -> None:
    provider = _Provider()
    renderer = _renderer(qtbot, provider)
    changes = _record(renderer)
    _click(qtbot, renderer, ON_OBJECT)

    provider.bounds = None
    renderer.display_frame(_frame(provider))
    assert renderer._selected_bounds is None

    provider.bounds = (0.60, 0.50, 0.20, 0.20)
    renderer.display_frame(_frame(provider))
    assert renderer._selected_bounds == provider.bounds
    assert changes == ["entity-1"]


def test_hover_chip_still_follows_the_pointer_while_an_object_is_selected(qtbot: QtBot) -> None:
    provider = _Provider()
    renderer = _renderer(qtbot, provider)
    _click(qtbot, renderer, ON_OBJECT)

    _move(renderer, OFF_OBJECT)
    assert not _chip(renderer).isVisible()
    _move(renderer, ON_OBJECT)
    assert _chip(renderer).isVisible()

    chip_position = _chip(renderer).pos()
    provider.bounds = (0.60, 0.50, 0.20, 0.20)
    renderer.display_frame(_frame(provider))
    _move(renderer, QPoint(400, 280))
    assert _chip(renderer).isVisible()
    assert _chip(renderer).pos() != chip_position


def test_select_target_marks_the_object_without_emitting(qtbot: QtBot) -> None:
    renderer = _renderer(qtbot, _Provider())
    changes = _record(renderer)

    renderer.select_target("entity-1")
    assert renderer._selected_bounds == (0.10, 0.10, 0.20, 0.20)
    renderer.select_target("other")  # Not in the frame: selected, but without a box.
    assert renderer._selected_bounds is None
    renderer.select_target(None)

    assert changes == []


def test_turning_object_interaction_off_clears_the_chip_and_the_selection(qtbot: QtBot) -> None:
    viewport = FrameViewport()
    viewport.resize(640, 480)
    viewport.show()
    qtbot.addWidget(viewport)
    viewport.display_frame(_frame(_Provider()))
    changes = _record(viewport)
    _move(viewport, ON_OBJECT)
    _click(qtbot, viewport, ON_OBJECT)

    settings = GlobalSettings()
    try:
        settings.set_overlay_enabled(OverlayPreference.HOVER, False)
        assert not _chip(viewport).isVisible()
        assert changes == ["entity-1", None]
        _click(qtbot, viewport, ON_OBJECT)
        assert changes == ["entity-1", None]
        viewport.select_target("entity-1")  # The panel moves to an object while interaction is off.
        assert viewport._selected_bounds is None
        settings.set_overlay_enabled(OverlayPreference.HOVER, True)
        assert viewport._selected_bounds == (0.10, 0.10, 0.20, 0.20)  # Turning it on boxes the kept selection.
        _move(viewport, ON_OBJECT)
        assert _chip(viewport).isVisible()
    finally:
        settings.set_overlay_enabled(OverlayPreference.HOVER, True)
