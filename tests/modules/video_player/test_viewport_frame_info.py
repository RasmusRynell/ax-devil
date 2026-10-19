"""Current frame identity and synchronization information in the viewport HUD."""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player import VideoFrame, VideoFrameWithOverlays, VideoOverlayData
from ax_devil.modules.video_player.engine.quick.preparation import PreparedDrawing
from ax_devil.modules.video_player.ui.viewport import FrameViewport


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


def _overlay_data() -> VideoOverlayData:
    return VideoOverlayData(
        drawing_generator=lambda _context, _settings: PreparedDrawing(),
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
    )


def _viewport(qtbot: QtBot) -> FrameViewport:
    widget = FrameViewport()
    qtbot.addWidget(widget, before_close_func=lambda widget: widget.cleanup())
    widget.show()
    return widget


def test_info_overlay_formats_exact_current_timestamps_only_when_visible(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    widget = _viewport(qtbot)
    formatted: list[VideoFrameWithOverlays] = []
    build = widget._build_info_overlay_stats

    def counted(frame: VideoFrameWithOverlays) -> dict[str, Any]:
        formatted.append(frame)
        return build(frame)

    monkeypatch.setattr(widget, "_build_info_overlay_stats", counted)

    widget.display_frame(_frame(overlays=_overlay_data()))
    assert not formatted
    widget.toggle_info_overlay()
    assert len(formatted) == 1

    # The info overlay paints these private stats; reading them avoids parsing painted text.
    expected = {
        "Status": "OK overlay is before frame",
        "Frame - overlay": "5000",
        "Frame id": "1",
        "Frame timestamp": "1000000",
        "Overlay id": "9",
        "Overlay timestamp": "995000",
        "Match type": "tolerated_past",
    }
    assert {key: widget._stats.get(key) for key in expected} == expected

    widget.hide()
    future_overlay = VideoOverlayData(
        drawing_generator=lambda _context, _settings: PreparedDrawing(),
        timestamp_monotime_us=1_005_000.0,
        overlay_id=10,
    )
    widget.display_frame(_frame(overlays=future_overlay))
    assert len(formatted) == 1
    widget.show()
    assert len(formatted) == 2

    assert widget._stats["Status"] == "VIOLATION overlay is 5000 after frame"
    assert widget._stats["Frame - overlay"] == "-5000"
    widget.toggle_info_overlay()
    widget.display_frame(_frame(overlays=None))
    assert len(formatted) == 2
