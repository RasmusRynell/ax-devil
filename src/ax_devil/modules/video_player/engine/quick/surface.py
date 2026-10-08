"""Widget-compatible Quick surface with native video, paths, and glyph rendering."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QRectF, Qt, QUrl
from PySide6.QtGui import QImage, QShowEvent
from PySide6.QtQuick import QSGRendererInterface
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.theme import VIDEO_CANVAS

from ..drawing import DrawingStyle, Paint
from .items import VideoTextureItem
from .overlay import QuickOverlay, ShapeComponents
from .preparation import DrawingBuffer, DrawingSettings, PreparedDrawing

_HOVER_HIGHLIGHT_STYLE = DrawingStyle(
    pen_r=255,
    pen_g=255,
    pen_b=255,
    pen_width=0.004,
    brush_r=255,
    brush_g=255,
    brush_b=255,
    brush_a=30,
)


class QuickSurface(QQuickWidget):
    """Shared display/export drawing surface; the host owns interaction and workflows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        try:
            self._initialize()
        except RuntimeError:
            self.hide()
            self.deleteLater()
            raise

    def _initialize(self) -> None:
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._apply_background()
        url = QUrl.fromLocalFile(str(Path(__file__).with_name("Surface.qml")))
        self.setSource(url)
        root = self.rootObject()
        if root is None:
            raise RuntimeError(f"Quick surface creation failed: {self.errors()}")
        self._root = root
        self.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self._video = VideoTextureItem(self._root)
        self._components = ShapeComponents(self.engine())
        self._overlays = QuickOverlay(self._root, self._components)
        self._highlight = QuickOverlay(self._root, self._components)
        self._drawing_buffer = DrawingBuffer(DrawingSettings(0, 0, 1))
        self._overlays.item.setZ(1)
        self._highlight.item.setZ(2)

    @property
    def backend_label(self) -> str:
        """Identify Qt's actual scene graph API in rendering diagnostics."""
        return f"Qt Quick ({self.quickWindow().rendererInterface().graphicsApi().name})"

    def set_frame_image(self, image: QImage, target: QRectF) -> None:
        """Queue the image and its viewport transform for the next scene graph sync."""
        self._video.setPosition(target.topLeft())
        self._video.setSize(target.size())
        self._video.set_image(image)
        self._overlays.item.setPosition(target.topLeft())
        self._highlight.item.setPosition(target.topLeft())

    def drawing_settings(self, target: QRectF, scale: float) -> DrawingSettings:
        """Describe all inputs affecting final geometry and glyph preparation."""
        viewport = QRectF(self.rect()).translated(-target.topLeft())
        api = self.quickWindow().rendererInterface().graphicsApi()
        return DrawingSettings(
            target.width(),
            target.height(),
            scale,
            self.logicalDpiY(),
            self.devicePixelRatioF(),
            api not in (QSGRendererInterface.GraphicsApi.Software, QSGRendererInterface.GraphicsApi.Unknown),
            (viewport.x(), viewport.y(), viewport.width(), viewport.height()),
        )

    def drawing_buffer(self, target: QRectF, scale: float) -> DrawingBuffer:
        """Begin preparation with this surface's bounded previous-frame reuse."""
        self._drawing_buffer.reset(self.drawing_settings(target, scale))
        return self._drawing_buffer

    def set_overlays(
        self,
        drawing: PreparedDrawing | None,
        opacity: float,
        target: QRectF,
        scale: float,
        highlight: tuple[float, float, float, float] | None,
    ) -> None:
        """Bind prepared output before Qt renders the corresponding video image."""
        self._overlays.present(drawing, opacity)
        highlight_drawing = None
        if highlight is not None:
            buffer = DrawingBuffer(self.drawing_settings(target, scale))
            x, y, width, height = (np.array([value]) for value in highlight)
            buffer.boxes(x, y, width, height, Paint.of(_HOVER_HIGHLIGHT_STYLE))
            highlight_drawing = buffer.finish()
        self._highlight.present(highlight_drawing, 1.0)

    def clear(self) -> None:
        """Release the submitted image and overlay references."""
        self._video.set_image(QImage())
        self._drawing_buffer = DrawingBuffer(DrawingSettings(0, 0, 1))
        self._overlays.present(None, 1)
        self._highlight.present(None, 1)

    def cleanup(self) -> None:
        """Release frame data and let Qt retire render-thread-owned resources."""
        self.clear()
        if self.quickWindow().isSceneGraphInitialized():
            # Consume the empty frame before Python can release texture adapters.
            # QQuickWidget's render control defers ordinary update() requests.
            self.grabFramebuffer()
        self.hide()
        self.quickWindow().setPersistentSceneGraph(False)
        self.quickWindow().releaseResources()

    def _apply_background(self) -> None:
        """Fill letterbox areas with the video canvas so nothing stale shows outside the image."""
        self.setClearColor(VIDEO_CANVAS)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Restore the clear color after Qt recreates the window during reparenting."""
        self._apply_background()
        super().showEvent(event)
