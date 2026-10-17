"""What the Objects page and the event page share when they list objects of the whole file."""

from __future__ import annotations

import re

from ax_devil.modules.data_sources.scene_history import ObjectHistory
from ax_devil.modules.video_viewer.media_tools.frame_labels import format_short_frame_time

_DIGITS = re.compile(r"(\d+)")


def id_order(text: str) -> tuple[tuple[int, str], ...]:
    """Return *text* as a sort key that orders embedded numbers by value, so ``"id9"`` comes before ``"id10"``."""
    return tuple((int(part), "") if part.isdigit() else (-1, part) for part in _DIGITS.split(text))


def seen_range(history: ObjectHistory) -> str:
    """Return when the object is first and last shown, such as ``0:03–1:12``."""
    start = format_short_frame_time(history.first_seen.timestamp_monotime_us)
    return f"{start}–{format_short_frame_time(history.last_seen.timestamp_monotime_us)}"
