"""What a hover card can show a frame of, and how each kind of source reads it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Protocol

from PySide6.QtGui import QImage

from ax_devil.core.cancellation import CancelToken
from ax_devil.core.device_connection import DeviceConnection
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import ImageSequenceConfig
from ax_devil.modules.thumbnails.camera_snapshot import SNAPSHOT_TIMEOUT_S as SNAPSHOT_TIMEOUT_S
from ax_devil.modules.thumbnails.camera_snapshot import read_snapshot
from ax_devil.modules.thumbnails.first_frame import cached_first_frame
from ax_devil.modules.thumbnails.scaling import THUMBNAIL_EDGE_PX as THUMBNAIL_EDGE_PX
from ax_devil.modules.thumbnails.scaling import fit as fit

OpenFrames = Callable[[str], QImage | None]
"""Content id -> the newest frame of the open viewer showing it (a null image until its first frame), or None when
no viewer shows it."""


class ThumbnailSource(Protocol):
    """Something a thumbnail can be read from; hashable, because it keys the memory cache."""

    fresh_s: ClassVar[float]
    """How long a read picture, or failure, is reused before it is read again."""

    def open_frame(self, open_frames: OpenFrames) -> QImage | None:
        """Return the picture an open viewer already has (null while it has none), or None when nothing is open."""
        ...

    def read(self, token: CancelToken) -> QImage:
        """Read the picture on a worker thread; raise when it cannot be read."""
        ...


@dataclass(frozen=True, slots=True)
class FileThumbnail:
    """The first frame of a video file or image sequence."""

    path: Path
    image_sequence: ImageSequenceConfig | None = field(default=None, compare=False)
    fresh_s: ClassVar[float] = 10.0

    def open_frame(self, open_frames: OpenFrames) -> None:
        """Return None: a file's first frame never depends on an open viewer."""
        return None

    def read(self, token: CancelToken) -> QImage:
        """Return the first frame, from the disk cache when the file is unchanged."""
        return cached_first_frame(self.path, self.image_sequence, token)


@dataclass(frozen=True, slots=True)
class CameraThumbnail:
    """The picture of a live stream: its open viewer's newest frame, else one snapshot from the camera.

    The snapshot is the camera's, whichever of its streams is asked about, so streams of one camera are the same
    source and share one read and one cooldown. Only the open-viewer lookup tells them apart.
    """

    content_id: str = field(compare=False)
    connection: DeviceConnection
    camera_head: int
    fresh_s: ClassVar[float] = 10.0

    def open_frame(self, open_frames: OpenFrames) -> QImage | None:
        """Return the open viewer's newest frame, so no second connection is made."""
        return open_frames(self.content_id)

    def read(self, token: CancelToken) -> QImage:
        """Ask the camera for one snapshot."""
        return read_snapshot(self.connection, self.camera_head, token)
