"""Render-metrics reporting for one video renderer."""

from __future__ import annotations

from itertools import count
from time import perf_counter

from PySide6.QtCore import QObject

from ax_devil.modules.diagnostics.render_metrics import FrameIdentity, PaintSample, get_render_metrics_store

from .data_types import VideoFrameWithOverlays
from .render_context import RenderContext

_VIEWER_IDS = count(1)


def frame_identity(frame: VideoFrameWithOverlays) -> FrameIdentity:
    """Return the identity and timestamp under which diagnostics track *frame*."""
    video = frame.frame
    timestamp = video.timestamp_monotime_us
    return FrameIdentity(video.frame_id, timestamp if timestamp is not None else video.timestamp * 1_000_000)


class RendererDiagnostics:
    """Registers one renderer with the render-metrics store and reports its frames and paint samples."""

    def __init__(self, owner: QObject) -> None:
        self._id = f"viewer-{next(_VIEWER_IDS)}"
        get_render_metrics_store().register(self._id, self._id.replace("-", " ").title())
        owner.destroyed.connect(lambda _=None, instance_id=self._id: get_render_metrics_store().remove(instance_id))

    def submit(self, frame: VideoFrameWithOverlays) -> None:
        """Report that *frame* was handed to the renderer."""
        get_render_metrics_store().submit(self._id, frame_identity(frame))

    def reset(self) -> None:
        """Forget the samples gathered so far."""
        get_render_metrics_store().reset(self._id)

    def remove(self) -> None:
        """Unregister the renderer."""
        get_render_metrics_store().remove(self._id)

    def set_label(self, label: str) -> None:
        """Associate rendering samples with the content/lane displayed by this viewer."""
        get_render_metrics_store().register(self._id, label)

    def set_sources(self, source_ids: tuple[str, ...]) -> None:
        """Associate this viewer with the source observations used to produce its frames."""
        get_render_metrics_store().set_sources(self._id, source_ids)

    def record(
        self,
        frame: VideoFrameWithOverlays,
        start_time: float,
        image_start: float,
        compose_start: float,
        draw_start: float,
        draw_end: float,
        context: RenderContext,
        *,
        primitive_count: int,
        submitted_count: int,
        opacity: float,
        backend: str,
    ) -> None:
        """Record one paint of *frame* from its phase timestamps."""
        completed_at = perf_counter()
        overlay = frame.overlays
        provider = overlay.metrics_provider if overlay is not None else None
        get_render_metrics_store().record(
            self._id,
            PaintSample(
                completed_at=completed_at,
                frame=frame_identity(frame),
                overlay=FrameIdentity(
                    overlay.overlay_id,
                    overlay.timestamp_monotime_us
                    if overlay.timestamp_monotime_us is not None
                    else overlay.timestamp * 1_000_000,
                )
                if overlay
                else None,
                overlay_reused=bool(overlay and overlay.metadata and overlay.metadata.get("overlay_reused", False)),
                paint_ms=(completed_at - start_time) * 1000,
                image_ms=(compose_start - image_start) * 1000,
                compose_ms=(draw_start - compose_start) * 1000,
                draw_ms=(draw_end - draw_start) * 1000,
                primitive_count=primitive_count,
                drawn_primitive_count=submitted_count if opacity > 0.0 else 0,
                generation=provider.latest_drawing_preparation_metrics() if provider else None,
                backend=backend,
                width=context.width,
                height=context.height,
            ),
            paint_started_at=start_time,
        )
