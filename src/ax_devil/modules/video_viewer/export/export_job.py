"""Synchronous video export workflow with owned rendering and output resources."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.data_sources.base import OverlayLookup
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_player.engine.quick.image_renderer import FrameImageRenderer
from ax_devil.modules.video_viewer.export.encoder import CompressionPreset, VideoEncoder
from ax_devil.modules.video_viewer.lane_grid import lane_grid_columns, lane_grid_rows
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistencePolicy
from ax_devil.modules.video_viewer.scene_frame_presenter import SceneFramePresenter

logger = get_logger(__name__)

# ponytail: fixed cap keeps tiled output playable; make it a dialog option if full-resolution grids are needed.
_MAX_TILED_SIZE = (3840, 2160)


@dataclass(frozen=True, slots=True)
class ExportLane:
    """One lane's inputs, frozen at export start."""

    name: str
    video_source: FileFrameSource
    presenter: SceneFramePresenter
    overlay_source: OverlayLookup | None = None
    overlay_policy: OverlayPersistencePolicy | None = None


class ExportJob:
    """Export selected lanes using frozen presentation inputs on the GUI thread."""

    def __init__(
        self,
        lanes: Sequence[ExportLane],
        output_path: Path,
        *,
        compression: CompressionPreset | None = None,
    ) -> None:
        if not lanes:
            raise ValueError("Export requires at least one lane")
        self._lanes = tuple(lanes)
        self._output_path = output_path
        self._compression = compression or CompressionPreset.default()
        self._temporary_path: Path | None = None
        self._cancelled = False

    def cancel(self) -> None:
        """Request cancellation before the next frame or destination replacement."""
        self._cancelled = True

    def run(self, progress: Callable[[int, int], None] | None = None) -> bool:
        """Export on the GUI thread; return False on cancellation and propagate failures.

        Progress reports zero-based frame index and total after encoding, every ten frames
        and on the final frame. The caller may pump events there and call cancel().
        Sources and presenters are borrowed; only rendering and output resources are owned.
        """
        lanes = self._lanes
        driver = lanes[0].video_source
        total = driver.total_frames
        fps = float(driver.fps)
        for lane in lanes:
            source_path = Path(lane.video_source.file_path)
            if self._output_path.resolve() == source_path.resolve() or (
                source_path.exists() and self._output_path.exists() and self._output_path.samefile(source_path)
            ):
                raise ValueError("Export destination must differ from the source video")

        encoder: VideoEncoder | None = None
        renderer: FrameImageRenderer | None = None
        held: list[QImage] = []
        try:
            renderer = FrameImageRenderer()
            for idx in range(total):
                if self._cancelled:
                    break

                # Lanes sharing a pooled source decode each frame once.
                decoded: dict[int, DecodedFrame | None] = {}
                rendered: list[QImage] = []
                for lane_index, lane in enumerate(lanes):
                    source = lane.video_source
                    if id(source) not in decoded:
                        decoded[id(source)] = source.read_decoded_frame(idx) if idx < source.total_frames else None
                    decoded_frame = decoded[id(source)]
                    if decoded_frame is None:
                        if lane_index == 0 or idx < source.total_frames:
                            raise RuntimeError(f"Failed to read frame {idx + 1} of {total} from {lane.name}")
                        # A shorter lane holds its last frame, as it does in the viewer.
                        rendered.append(held[lane_index])
                        continue
                    image = self._decoded_frame_to_image(decoded_frame)
                    rendered.append(renderer.render_frame(self._present_frame(lane, image, idx, decoded_frame)))
                held = rendered
                canvas = self._compose(rendered, [lane.name for lane in lanes])

                if encoder is None:
                    # h.264 yuv420p requires even dimensions — round down if source is odd
                    width = canvas.width() & ~1
                    height = canvas.height() & ~1
                    if width == 0 or height == 0:
                        raise RuntimeError(f"Frame too small to encode: {canvas.width()}x{canvas.height()}")
                    with tempfile.NamedTemporaryFile(
                        prefix=f".{self._output_path.stem}-", suffix=".mp4", dir=self._output_path.parent, delete=False
                    ) as temporary:
                        self._temporary_path = Path(temporary.name)
                    encoder = VideoEncoder(
                        self._temporary_path, width=width, height=height, fps=fps, compression=self._compression
                    )

                driver_frame = decoded[id(driver)]
                assert driver_frame is not None
                encoder.write_frame(canvas, timestamp_us=driver_frame.timestamp_us, duration_s=driver_frame.duration_s)

                if progress is not None and (idx % 10 == 0 or idx == total - 1):
                    progress(idx, total)

            if encoder is None:
                return not self._cancelled
            if self._cancelled:
                encoder.close()
                return False
            encoder.finish()
            assert self._temporary_path is not None
            self._temporary_path.replace(self._output_path)
            self._temporary_path = None
            return True
        except BaseException:
            try:
                if encoder is not None:
                    encoder.close()
            except Exception as exc:
                logger.warning(f"Failed to close partial export: {exc}")
            raise
        finally:
            try:
                if renderer is not None:
                    renderer.cleanup()
                    renderer.deleteLater()
            finally:
                self._cleanup_partial_file()

    @staticmethod
    def _compose(images: list[QImage], names: list[str]) -> QImage:
        """Tile lane images in the viewer's lane grid, each labelled with its lane name."""
        if len(images) == 1:
            return images[0]
        columns = lane_grid_columns(len(images))
        rows = lane_grid_rows(len(images))
        cell_width, cell_height = images[0].width(), images[0].height()
        scale = min(1.0, _MAX_TILED_SIZE[0] / (columns * cell_width), _MAX_TILED_SIZE[1] / (rows * cell_height))
        cell_width, cell_height = int(cell_width * scale), int(cell_height * scale)

        canvas = QImage(columns * cell_width, rows * cell_height, QImage.Format.Format_RGB888)
        canvas.fill(Qt.GlobalColor.black)
        painter = QPainter(canvas)
        try:
            font = painter.font()
            font.setPixelSize(max(12, cell_height // 30))
            painter.setFont(font)
            painter.setPen(Qt.GlobalColor.white)
            for index, (image, name) in enumerate(zip(images, names)):
                row, column = divmod(index, columns)
                cell = QRect(column * cell_width, row * cell_height, cell_width, cell_height)
                scaled = image.scaled(
                    cell.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                x = cell.x() + (cell_width - scaled.width()) // 2
                painter.drawImage(x, cell.y() + (cell_height - scaled.height()) // 2, scaled)
                flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                label = painter.boundingRect(cell.adjusted(6, 4, -6, -4), flags, name)
                painter.fillRect(label.adjusted(-4, -2, 4, 2), QColor(0, 0, 0, 160))
                painter.drawText(label, flags, name)
        finally:
            painter.end()
        return canvas

    @staticmethod
    def _decoded_frame_to_image(decoded_frame: DecodedFrame) -> QImage:
        """Convert one decoded frame from the video source into a QImage."""
        frame_rgb = np.asarray(decoded_frame.pixels)
        height, width, _ = frame_rgb.shape
        bytes_per_line = 3 * width
        frame_rgb = np.ascontiguousarray(frame_rgb)
        # .copy() detaches from the numpy buffer so the QImage owns its data
        return QImage(frame_rgb.data, width, height, bytes_per_line, QImage.Format.Format_RGB888).copy()

    @staticmethod
    def _present_frame(
        lane: ExportLane,
        image: QImage,
        frame_index: int,
        decoded_frame: DecodedFrame,
    ) -> VideoFrameWithOverlays:
        """Run image through the lane's presenter pipeline with overlay lookup."""
        frame_id = FrameIdentifier(sequence_id=frame_index, timestamp_monotime_us=decoded_frame.timestamp_us)
        frame_data = FrameData(content=image, frame_id=frame_id, metadata={})

        overlay_data: OverlayData | None = None
        overlay_metadata: dict[str, float | bool] | None = None
        if lane.overlay_source is not None:
            if lane.overlay_policy is not None:
                selection = lane.overlay_policy.select_from_source(lane.overlay_source, frame_id)
                overlay_data = selection.overlay
                overlay_metadata = selection.to_metadata()
            else:
                overlay_data = lane.overlay_source.get_overlay_at_frame(frame_id)

        presentation = lane.presenter.prepare_frame(frame_data, overlay_data, overlay_metadata=overlay_metadata)
        return presentation.display_frame

    def _cleanup_partial_file(self) -> None:
        """Remove incomplete output file on cancel or error."""
        try:
            if self._temporary_path is not None:
                self._temporary_path.unlink(missing_ok=True)
                self._temporary_path = None
        except OSError as e:
            logger.warning(f"Failed to remove partial export file: {e}")
