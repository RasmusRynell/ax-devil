"""Tests for video renderer debug metric identity."""

from __future__ import annotations

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ax_devil.modules.diagnostics.metrics_store import set_metrics_enabled
from ax_devil.modules.diagnostics.render_metrics import ViewerSnapshot, get_render_metrics_store
from ax_devil.modules.video_player import VideoFrame, VideoFrameWithOverlays, VideoOverlayData
from ax_devil.modules.video_player.engine.data_types import DrawingPreparationMetrics
from ax_devil.modules.video_player.engine.quick.preparation import PreparedDrawing
from ax_devil.modules.video_player.ui.viewport import FrameViewport


class _StaticMetricsProvider:
    def __init__(self, metrics: DrawingPreparationMetrics) -> None:
        self._metrics = metrics

    def latest_drawing_preparation_metrics(self) -> DrawingPreparationMetrics:
        return self._metrics


def _image() -> QImage:
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(0)
    return image


def _frame(*, overlays: VideoOverlayData | None) -> VideoFrameWithOverlays:
    return VideoFrameWithOverlays(
        frame=VideoFrame(
            image=_image(),
            timestamp=1.0,
            timestamp_monotime_us=1_000_000.0,
            frame_id=1,
            metadata={
                "video_timestamp_source": "pts_time_base_minus_first_pts",
                "video_pts": 3000,
                "video_first_pts": 0,
                "video_pts_delta": 3000,
                "video_time_base": "1/3000",
                "video_period_after_s": 0.03333333333333333,
                "video_period_source": "pts_delta",
            },
        ),
        overlays=overlays,
    )


def _overlay_data(*, metrics_provider: _StaticMetricsProvider | None) -> VideoOverlayData:
    return VideoOverlayData(
        drawing_generator=lambda _context, _settings: PreparedDrawing(),
        timestamp=1.0,
        timestamp_monotime_us=995_000.0,
        overlay_id=9,
        metadata={
            "requested_timestamp_us": 1_000_000,
            "matched_timestamp_us": 995_000,
            "timestamp_match_type": "tolerated_past",
            "timestamp_fallback_mode": "previous_with_tolerance",
            "timestamp_tolerance_us": 10_000,
            "timestamp_effective_tolerance_us": 10_000,
            "overlay_alignment_basis": "timestamp",
            "timestamp_offset_us": -5_000,
            "requested_sequence_id": 1,
            "matched_sequence_id": 9,
        },
        metrics_provider=metrics_provider,
    )


def _snapshot_metrics(widget: FrameViewport) -> ViewerSnapshot:
    return next(item for item in get_render_metrics_store().snapshot() if item.viewer_id == widget._metrics_instance_id)


def _paint(widget: FrameViewport) -> None:
    widget.refresh_last_frame()
    QApplication.processEvents()


def test_frame_viewports_have_unique_metrics_ids(qtbot: QtBot) -> None:
    first = FrameViewport()
    second = FrameViewport()
    qtbot.addWidget(first)
    qtbot.addWidget(second)

    assert first._metrics_instance_id != second._metrics_instance_id


def test_frame_viewport_info_overlay_stats_use_exact_source_timestamps(qtbot: QtBot) -> None:
    widget = FrameViewport()
    qtbot.addWidget(widget)

    widget.display_frame(_frame(overlays=_overlay_data(metrics_provider=None)))

    assert widget._stats == {
        "Timing": None,
        "Unit": "microseconds",
        "Status": "OK overlay is before frame",
        "Frame - overlay": "5000",
        "Frame": None,
        "Frame id": "1",
        "Frame timestamp": "1000000",
        "Timestamp source": "pts_time_base_minus_first_pts",
        "PTS": 3000,
        "First PTS": 0,
        "PTS delta": 3000,
        "Time base": "1/3000",
        "Period after": "33333.333333333336",
        "Period source": "pts_delta",
        "Overlay": None,
        "Overlay id": "9",
        "Overlay timestamp": "995000",
        "Requested timestamp": 1_000_000,
        "Matched timestamp": 995_000,
        "Match type": "tolerated_past",
        "Fallback mode": "previous_with_tolerance",
        "Tolerance": 10_000,
        "Effective tolerance": 10_000,
        "Alignment basis": "timestamp",
        "Lookup offset": -5_000,
        "Requested sequence": 1,
        "Matched sequence": 9,
    }


def test_frame_viewport_info_overlay_flags_future_overlay_violation(qtbot: QtBot) -> None:
    widget = FrameViewport()
    qtbot.addWidget(widget)

    future_overlay = VideoOverlayData(
        drawing_generator=lambda _context, _settings: PreparedDrawing(),
        timestamp=1.005,
        timestamp_monotime_us=1_005_000.0,
        overlay_id=10,
    )
    widget.display_frame(_frame(overlays=future_overlay))

    assert widget._stats["Status"] == "VIOLATION overlay is 5000 after frame"
    assert widget._stats["Frame - overlay"] == "-5000"


def test_paint_samples_keep_frame_identity_and_clear_missing_preparation(qtbot: QtBot) -> None:
    set_metrics_enabled(True)
    widget = FrameViewport()
    qtbot.addWidget(widget)
    widget.resize(320, 240)
    provider = _StaticMetricsProvider(
        DrawingPreparationMetrics(
            filter_time_ms=1.25,
            generation_time_ms=2.5,
            input_entity_count=8,
            filtered_entity_count=2,
            primitive_counts=(("text", 7), ("polygon", 3)),
        )
    )
    widget.display_frame(_frame(overlays=_overlay_data(metrics_provider=provider)))
    _paint(widget)
    first = _snapshot_metrics(widget)
    assert first.last is not None
    sample = first.last.sample
    assert sample.generation == provider.latest_drawing_preparation_metrics()
    assert sample.frame.sequence == 1
    assert sample.frame.timestamp_us == 1_000_000
    assert sample.overlay is not None and sample.overlay.sequence == 9
    assert sample.paint_ms >= sample.image_ms + sample.compose_ms + sample.draw_ms
    assert first.last.new_frame
    assert first.last.submission_delay_ms is not None

    _paint(widget)
    repeated = _snapshot_metrics(widget)
    assert repeated.last is not None
    assert not repeated.last.new_frame
    assert repeated.last.submission_delay_ms is None

    for overlay in (None, _overlay_data(metrics_provider=None)):
        widget.display_frame(_frame(overlays=overlay))
        _paint(widget)
        cleared = _snapshot_metrics(widget)
        assert cleared.last is not None
        assert cleared.last.sample.generation is None
        assert cleared.last.sample.timings["filter"] is None
        assert cleared.last.sample.primitive_count == 0


def test_renderer_reset_cleanup_and_disabled_capture(qtbot: QtBot) -> None:
    widget = FrameViewport()
    qtbot.addWidget(widget)
    widget.set_diagnostics_label("Camera A")
    widget.display_frame(_frame(overlays=None))
    _paint(widget)
    assert _snapshot_metrics(widget).last is not None
    widget.clear()
    assert _snapshot_metrics(widget).last is None
    assert _snapshot_metrics(widget).submitted is None
    set_metrics_enabled(False)
    try:
        widget.display_frame(_frame(overlays=None))
        _paint(widget)
        assert _snapshot_metrics(widget).last is None
    finally:
        set_metrics_enabled(True)
    _paint(widget)
    assert _snapshot_metrics(widget).last is not None
    widget.cleanup()
    assert widget._metrics_instance_id not in {item.viewer_id for item in get_render_metrics_store().snapshot()}
