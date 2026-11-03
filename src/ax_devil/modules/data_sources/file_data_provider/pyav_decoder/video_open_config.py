"""Configuration for ``av.open()`` calls throughout the PyAV decoder stack.

All layers that need to open a container accept an optional
:class:`VideoOpenConfig` instead of separate ``format`` / ``options`` keyword
arguments.  This keeps the parameter surface small and extensible without
signature changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class VideoOpenConfig:
    """Extra arguments forwarded to ``av.open()``.

    Attributes:
        av_format: Demuxer / format hint (e.g. ``"image2"`` for image sequences).
        av_options: Dict of demuxer options (e.g. ``{"framerate": "30", "start_number": "1"}``).
    """

    av_format: str | None = None
    av_options: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImageSequenceConfig:
    """Metadata needed to open an image sequence as a video source.

    Callers provide this when the "video" is actually a directory of numbered
    image files (e.g. MOT Challenge ``img1/%06d.jpg``).  The config carries
    both the demuxer hints for PyAV and the video-level metadata that would
    normally come from ``ffprobe``.
    """

    fps: float
    width: int
    height: int
    total_frames: int
    start_number: int = 1

    def to_video_open_config(self) -> VideoOpenConfig:
        """Build the low-level :class:`VideoOpenConfig` for ``av.open()``."""
        return VideoOpenConfig(
            av_format="image2",
            av_options={
                "framerate": str(self.fps),
                "start_number": str(self.start_number),
            },
        )
