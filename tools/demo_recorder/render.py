"""Style a reusable raw take and export local MP4, WebP and preview artifacts."""

from __future__ import annotations

import subprocess
from pathlib import Path

import av
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath

from ax_devil.core.time_units import seconds_to_us
from ax_devil.modules.settings.logging_config import get_logger, setup_logging
from ax_devil.modules.video_viewer.export.encoder import CompressionPreset, VideoEncoder

logger = get_logger(__name__)


def style_frame(source: QImage, style: str) -> QImage:
    """Fit the whole app into a rounded, shadowed window over a dark blurred background."""
    if style == "plain":
        return source.copy()
    width, height = source.width(), source.height()
    smooth = Qt.TransformationMode.SmoothTransformation
    # A downsample/upsample blur keeps the effect independent of optional imaging packages.
    image = (
        source.scaled(24, 16, Qt.AspectRatioMode.IgnoreAspectRatio, smooth)
        .scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, smooth)
        .convertToFormat(QImage.Format.Format_RGB32)
    )
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.fillRect(image.rect(), QColor(11, 16, 32, 190))
    padding = width * 0.03
    target = QRectF(padding, padding, width - padding * 2, height - padding * 2)
    # Fit without changing the application's aspect ratio.
    fit = min(target.width() / width, target.height() / height)
    target = QRectF((width - width * fit) / 2, (height - height * fit) / 2, width * fit, height * fit)
    for spread in range(16, 0, -2):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 12))
        painter.drawRoundedRect(target.adjusted(-spread, -spread + 5, spread, spread + 5), 14 + spread, 14 + spread)
    clip = QPainterPath()
    clip.addRoundedRect(target, 14, 14)
    painter.setClipPath(clip)
    painter.drawImage(target, source)
    painter.end()
    return image


def render(source: Path, destination: Path, style: str) -> None:
    """Render a take once and derive its looping WebP and representative preview."""
    setup_logging(console_only=True, console_log_level="INFO")
    app = QGuiApplication.instance() or QGuiApplication([])
    assert app is not None
    partial = destination / "demo.partial.mp4"
    encoder: VideoEncoder | None = None
    with av.open(str(source)) as container:
        stream = container.streams.video[0]
        fps = float(stream.average_rate or 30)
        try:
            for index, frame in enumerate(container.decode(stream)):
                array = frame.to_ndarray(format="rgb24")
                image = QImage(
                    array.data, frame.width, frame.height, array.strides[0], QImage.Format.Format_RGB888
                ).copy()
                image = style_frame(image, style)
                if encoder is None:
                    encoder = VideoEncoder(
                        partial,
                        width=image.width(),
                        height=image.height(),
                        fps=fps,
                        compression=CompressionPreset.high_quality(),
                    )
                encoder.write_frame(image, timestamp_us=seconds_to_us(index / fps))
                if index == 0 or index == round(fps * 2):
                    image.save(str(destination / "preview.png"))
            if encoder is None:
                raise ValueError("The raw take contains no video frames")
        finally:
            if encoder is not None:
                encoder.finish()
    partial.rename(destination / "demo.mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-n",
            "-i",
            str(destination / "demo.mp4"),
            "-vf",
            "fps=15,scale=min(1280\\,iw):-2:flags=lanczos",
            "-c:v",
            "libwebp",
            "-quality",
            "72",
            "-loop",
            "0",
            str(destination / "demo.webp"),
        ],
        check=True,
    )
    logger.info(f"Wrote {destination / 'demo.mp4'} and {destination / 'demo.webp'}")
