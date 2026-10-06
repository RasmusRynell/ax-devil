"""Shared frame labels and highlight for media tools that point at video frames."""

from __future__ import annotations

from PySide6.QtGui import QColor

from ax_devil.core.data_types import FrameIdentifier

CURRENT_FRAME_HIGHLIGHT = QColor(78, 142, 247, 70)
"""Background of rows that belong to the displayed frame."""

_US_PER_DAY = 86_400_000_000


def format_frame_time(timestamp_us: float) -> str:
    """Format a frame timestamp as HH:MM:SS.mmm: video position offline, UTC clock time live."""
    hours, minutes, seconds, milliseconds = _clock_parts(timestamp_us)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def format_short_frame_time(timestamp_us: float) -> str:
    """Format a frame timestamp compactly as M:SS, with hours only when present."""
    hours, minutes, seconds, _milliseconds = _clock_parts(timestamp_us)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _clock_parts(timestamp_us: float) -> tuple[int, int, int, int]:
    total_ms = int(timestamp_us % _US_PER_DAY) // 1000
    seconds, milliseconds = divmod(total_ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return hours, minutes, seconds, milliseconds


def frame_link(frame_id: FrameIdentifier) -> str:
    """Return rich text linking to *frame_id*; the link target is the frame index."""
    return (
        f'<a href="{frame_id.sequence_id}">#{frame_id.sequence_id} '
        f"{format_frame_time(frame_id.timestamp_monotime_us)}</a>"
    )
