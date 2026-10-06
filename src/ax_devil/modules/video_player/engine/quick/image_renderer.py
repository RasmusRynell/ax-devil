"""Native-size image export using the same Quick scene as the viewer."""

from math import ceil

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage

from ..data_types import VideoFrameWithOverlays
from ..render_context import RenderContext
from .surface import QuickSurface


class FrameImageRenderer(QuickSurface):
    """Render export frames on the GUI thread without displaying a window."""

    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)

    def render_frame(self, frame: VideoFrameWithOverlays) -> QImage:
        """Render exact source pixels, independent of the desktop's display scaling."""
        image = frame.frame.image
        width, height = image.width(), image.height()
        context = RenderContext.create(width, height)
        ratio = self.devicePixelRatioF()
        self.resize(ceil(width / ratio), ceil(height / ratio))
        if not self.isVisible():
            self.show()
        target = QRectF(0, 0, width / ratio, height / ratio)
        drawing, opacity = frame.prepare_overlays(context, self.drawing_buffer(target, context.scale_factor / ratio))
        self.set_frame_image(image, target)
        self.set_overlays(drawing, opacity, target, context.scale_factor / ratio, None)
        rendered = self.grabFramebuffer()
        if rendered.width() < width or rendered.height() < height:
            raise RuntimeError(f"Quick export produced {rendered.size()} for a {width}x{height} frame")
        rendered.setDevicePixelRatio(1)
        return rendered if rendered.size() == image.size() else rendered.copy(0, 0, width, height)
