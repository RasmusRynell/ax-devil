"""Tests for Quick frame export at native image dimensions."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    KnownClassificationType,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.scene.rendering.cache import CachedSceneOverlay
from ax_devil.modules.video_player.engine.data_types import (
    VideoFrame,
    VideoFrameWithOverlays,
    VideoOverlayData,
)
from ax_devil.modules.video_player.engine.quick.image_renderer import FrameImageRenderer
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, PreparedDrawing
from ax_devil.modules.video_player.engine.render_context import RenderContext
from tests.drawing_helpers import prepare_calls


def _make_solid_frame(width: int = 100, height: int = 80, color: int = 0xFF0000FF) -> QImage:
    """Create a solid-color QImage for testing."""
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(color)
    return image


def _build_scene_with_entity() -> Scene:
    """Build a minimal Scene with one human entity and a bounding box."""
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId("test-entity"))
    entity.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.1, 0.1, 0.3, 0.3, allow_outside=True),
            classification=[Classification(type=KnownClassificationType.Human.value, score=Score(0.9))],
            frame_number=0,
            timestamp=datetime.now(timezone.utc),
        )
    )
    scene.add_entity(entity)
    return scene


def _make_display_frame(image: QImage, scene: Scene | None = None) -> VideoFrameWithOverlays:
    """Build a VideoFrameWithOverlays for testing."""
    video_frame = VideoFrame(image=image, timestamp=0.0, frame_id=0)
    overlay = None
    if scene is not None:
        cached = CachedSceneOverlay(scene=scene)
        overlay = VideoOverlayData(
            drawing_generator=cached.prepare_drawing,
            timestamp=0.0,
        )
    return VideoFrameWithOverlays(
        frame=video_frame,
        overlays=overlay,
    )


def _raise_render_error(_context: RenderContext, _buffer: DrawingBuffer) -> PreparedDrawing:
    raise ValueError("bad overlay")


@pytest.fixture
def renderer(qtbot: QtBot) -> FrameImageRenderer:
    """Own the export renderer throughout each test."""
    widget = FrameImageRenderer()
    qtbot.addWidget(widget)
    return widget


class TestRenderToImageNoOverlay:
    """When no overlay is present, export returns frame image pixels."""

    def test_returns_image_with_same_content_when_no_overlay(self, renderer: FrameImageRenderer) -> None:
        frame = _make_solid_frame(10, 10, color=0xFFFF0000)
        display = _make_display_frame(frame, scene=None)
        result = renderer.render_frame(display)
        assert result.size() == frame.size()
        assert result.pixelColor(5, 5) == frame.pixelColor(5, 5)


@pytest.mark.usefixtures("qapp")
class TestRenderToImageWithOverlay:
    """When scene has entities, export draws overlays."""

    def test_image_differs_from_input_when_overlay_drawn(self, renderer: FrameImageRenderer) -> None:
        frame = _make_solid_frame(200, 150, color=0xFF000000)  # solid black
        original = frame.copy()
        scene = _build_scene_with_entity()
        display = _make_display_frame(frame, scene=scene)
        result = renderer.render_frame(display)
        assert result.size() == original.size()
        assert frame == original
        assert result.pixelColor(180, 130) == original.pixelColor(180, 130)
        # Check a pixel inside the entity bounding box region (0.1*200=20, 0.1*150=15)
        pixel = result.pixelColor(30, 25)
        # At least one channel should differ from pure black (overlay drawn something)
        assert pixel.red() > 0 or pixel.green() > 0 or pixel.blue() > 0

    def test_export_raises_when_overlay_generator_fails(self, renderer: FrameImageRenderer) -> None:
        frame = VideoFrame(image=_make_solid_frame(), timestamp=0.0, frame_id=0)
        display = VideoFrameWithOverlays(
            frame=frame,
            overlays=VideoOverlayData(
                drawing_generator=_raise_render_error,
                timestamp=0.0,
            ),
        )

        with pytest.raises(ValueError, match="bad overlay"):
            renderer.render_frame(display)


@pytest.mark.parametrize("width,height", [(203, 101), (64, 48), (17, 13)])
def test_export_keeps_native_pixels_and_context_under_display_scaling(
    renderer: FrameImageRenderer, width: int, height: int
) -> None:
    """Keep pixel dimensions, edge pixels, and generator context independent of monitor DPI."""
    from PySide6.QtGui import QColor

    from ax_devil.modules.video_player.engine.drawing import DrawingStyle
    from tests.drawing_helpers import BoxCall

    image = _make_solid_frame(width, height)
    image.setPixelColor(width - 1, height - 1, QColor("green"))
    contexts: list[RenderContext] = []

    def generate(context: RenderContext, buffer: DrawingBuffer) -> PreparedDrawing:
        contexts.append(context)
        return prepare_calls([BoxCall(0.1, 0.1, 0.5, 0.5, DrawingStyle(pen_width=0, brush_r=255, brush_a=255))], buffer)

    frame = VideoFrameWithOverlays(VideoFrame(image, 0), VideoOverlayData(generate, 0))
    rendered = renderer.render_frame(frame)
    assert rendered.size() == image.size()
    assert rendered.devicePixelRatio() == 1
    assert contexts == [RenderContext.create(width, height)]
    assert rendered.pixelColor(width // 3, height // 3) == QColor("red")
    assert rendered.pixelColor(width - 1, height - 1) == QColor("green")
    assert image.pixelColor(width // 3, height // 3) == QColor("blue")
    cleared = renderer.render_frame(_make_display_frame(image))
    assert cleared.pixelColor(width // 3, height // 3) == QColor("blue")
