"""Shared frame labels and highlight for tools that point at video frames."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.core.time_units import us_to_datetime, us_to_ms

_HIGHLIGHT_ALPHA = 0.27


def current_frame_highlight(palette: QPalette) -> QColor:
    """Return the background of rows that belong to the displayed frame: the palette's highlight, see-through."""
    color = QColor(palette.color(QPalette.ColorRole.Highlight))
    color.setAlphaF(_HIGHLIGHT_ALPHA)
    return color


def format_frame_time(timestamp_us: int) -> str:
    """Format a frame timestamp as HH:MM:SS.mmm: video position offline, UTC clock time live."""
    hours, minutes, seconds, milliseconds = _clock_parts(timestamp_us)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def format_short_frame_time(timestamp_us: int) -> str:
    """Format a frame timestamp compactly as M:SS, with hours only when present."""
    hours, minutes, seconds, _milliseconds = _clock_parts(timestamp_us)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _clock_parts(timestamp_us: int) -> tuple[int, int, int, int]:
    moment = us_to_datetime(timestamp_us)
    return moment.hour, moment.minute, moment.second, int(us_to_ms(moment.microsecond))


def frame_link(frame_id: FrameIdentifier) -> str:
    """Return rich text linking to *frame_id*; the link target is the frame index."""
    return f'<a href="{frame_id.sequence_id}">#{frame_id.sequence_id} {format_frame_time(frame_id.timestamp_us)}</a>'
