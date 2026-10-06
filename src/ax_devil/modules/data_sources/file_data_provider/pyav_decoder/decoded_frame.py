"""Decoded frame payload returned by the PyAV reader."""

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    """Decoded RGB pixels plus source timing facts for one video frame.

    ``duration_s`` is the decoded source duration, when available. ``period_after_s``
    is the indexed playback delay, which may use the preceding gap for the last frame.
    """

    frame_index: int
    pixels: np.ndarray
    timestamp_us: float
    period_after_s: float | None = None
    source_timing_metadata: dict[str, Any] = field(default_factory=dict)
    duration_s: float | None = None
