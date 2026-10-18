"""Widget-compatible Quick surface with native video and overlay rendering."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import QImage, QShowEvent
from PySide6.QtQuick import QSGRendererInterface
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.theme import VIDEO_CANVAS
from ax_devil.modules.settings.logging_config import get_logger

from ..drawing import Paint
from .items import VideoTextureItem
from .overlay_material import OverlayItem
from .preparation import EMPTY_DRAWING, DrawingBuffer, DrawingSettings, PreparedDrawing

logger = get_logger(__name__)

_HOVER_HIGHLIGHT = Paint(
    pen_width=0.004, pen_r=255, pen_g=255, pen_b=255, brush_r=255, brush_g=255, brush_b=255, brush_a=30
)
# Heavier than any catalog box, so the selected object reads at a glance; owned here, not by catalogs.
_SELECTED = Paint(pen_r=255, pen_g=255, pen_b=255, pen_width=0.009)

Bounds = tuple[float, float, float, float]  # Normalized (x, y, width, height)


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
        self._overlay = OverlayItem(self._root)
        self._highlight = OverlayItem(self._root)
        self._overlay.setZ(1)
        self._highlight.setZ(2)
        self.quickWindow().sceneGraphInitialized.connect(self._check_graphics_api)

    @property
    def draws_overlays(self) -> bool:
        """Whether Qt Quick runs on a graphics API; its software fallback cannot draw the overlay shader."""
        api = self.quickWindow().rendererInterface().graphicsApi()
        return api not in (QSGRendererInterface.GraphicsApi.Software, QSGRendererInterface.GraphicsApi.Unknown)

    def _check_graphics_api(self) -> None:
        if not self.draws_overlays:
            logger.error(
                "Qt Quick fell back to software rendering, so overlays will not draw. Install a working GPU "
                "driver (Linux: LIBGL_ALWAYS_SOFTWARE=1 uses Mesa; Windows: QSG_RHI_PREFER_SOFTWARE_RENDERER=1)."
            )

    @property
    def backend_label(self) -> str:
        """Identify Qt's actual scene graph API in rendering diagnostics."""
        return f"Qt Quick ({self.quickWindow().rendererInterface().graphicsApi().name})"

    def set_frame_image(self, image: QImage, target: QRectF) -> None:
        """Queue the image and its viewport transform for the next scene graph sync."""
        self._video.setPosition(target.topLeft())
        self._video.setSize(target.size())
        self._video.set_image(image)
        self._overlay.setPosition(target.topLeft())
        self._highlight.setPosition(target.topLeft())

    def drawing_settings(self, target: QRectF, scale: float) -> DrawingSettings:
        """Describe all inputs affecting final geometry and glyph preparation."""
        viewport = QRectF(self.rect()).translated(-target.topLeft())
        return DrawingSettings(
            target.width(),
            target.height(),
            scale,
            self.logicalDpiY(),
            self.devicePixelRatioF(),
            (viewport.x(), viewport.y(), viewport.width(), viewport.height()),
        )

    def drawing_buffer(self, target: QRectF, scale: float) -> DrawingBuffer:
        """Begin preparing a frame for this surface."""
        return DrawingBuffer(self.drawing_settings(target, scale))

    def set_overlays(
        self,
        drawing: PreparedDrawing | None,
        opacity: float,
        target: QRectF,
        scale: float,
        hovered: Bounds | None,
        selected: Bounds | None = None,
    ) -> None:
        """Bind prepared output before Qt renders the corresponding video image.

        *hovered* and *selected* are normalized boxes drawn above the overlays in their own styles.
        """
        extent = QSizeF(max(0.0, self.width() - target.x()), max(0.0, self.height() - target.y()))
        for item in (self._overlay, self._highlight):
            item.setSize(extent)
        self._overlay.present(EMPTY_DRAWING if drawing is None else drawing, opacity)
        highlight = EMPTY_DRAWING
        if hovered is not None or selected is not None:
            buffer = DrawingBuffer(self.drawing_settings(target, scale))
            for bounds, paint in ((selected, _SELECTED), (hovered, _HOVER_HIGHLIGHT)):
                if bounds is not None:
                    x, y, width, height = (np.array([value]) for value in bounds)
                    buffer.boxes(x, y, width, height, paint)
            highlight = buffer.finish()
        self._highlight.present(highlight, 1.0)

    def clear(self) -> None:
        """Release the submitted image and overlay references."""
        self._video.set_image(QImage())
        self._overlay.present(EMPTY_DRAWING, 1)
        self._highlight.present(EMPTY_DRAWING, 1)

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
