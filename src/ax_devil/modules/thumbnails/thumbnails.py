"""The GUI-thread service that answers hover cards with pictures."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import QObject
from PySide6.QtGui import QImage

from ax_devil.modules.background_jobs import BackgroundJobs, JobContext, LatestRequest, background_jobs
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.thumbnails.scaling import THUMBNAIL_EDGE_PX, fit
from ax_devil.modules.thumbnails.sources import OpenFrames, ThumbnailSource

logger = get_logger(__name__)

REMEMBERED_IMAGES = 48
OnImage = Callable[[QImage | None], None]


def _read(source: ThumbnailSource, job: JobContext[None]) -> QImage | None:
    """Read *source* on a worker thread; a failure is a missing picture, not an error."""
    try:
        return source.read(job.token)
    except Exception as error:
        logger.debug(f"No thumbnail → {source}: {error}")
        return None


@dataclass(slots=True)
class _Memory:
    """What is known of a source: when it was last asked for, and the last picture it gave."""

    attempted_at: float
    image: QImage | None = None


class Thumbnails(QObject):
    """Pictures for hover cards: an open stream's newest frame, else a read remembered in memory for a while.

    Reads run one at a time in the background; asking for another source cancels the one in progress. A source is
    not read again within its ``fresh_s`` of the last read being started, even when that read was cancelled. Everything
    here is used from the GUI thread only.
    """

    def __init__(
        self, open_frames: OpenFrames, jobs: BackgroundJobs | None = None, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._open_frames = open_frames
        self._latest = LatestRequest(self)
        self._scope = (jobs or background_jobs("thumbnails", max_workers=2)).scope(self)
        self._memory: OrderedDict[ThumbnailSource, _Memory] = OrderedDict()
        self._reading: ThumbnailSource | None = None
        self._on_image: OnImage | None = None

    def request(self, source: ThumbnailSource, on_image: OnImage) -> None:
        """Deliver *source*'s picture to *on_image*: now when it is known, else when it has been read.

        *on_image* gets None when the picture cannot be had. A stale picture is delivered first and replaced once it
        has been read again. Asking again for a source that is being read only replaces *on_image*.
        """
        frame = source.open_frame(self._open_frames)
        if frame is not None:
            if not frame.isNull():
                if source == self._reading:  # A snapshot still on its way must not replace this newer frame.
                    self._latest.cancel()
                    self._reading = self._on_image = None
                on_image(fit(frame, THUMBNAIL_EDGE_PX))
            return
        memory = self._memory.get(source)
        image = memory.image if memory is not None else None
        if source == self._reading:
            self._on_image = on_image
            if image is not None:
                on_image(image)
            return
        fresh = memory is not None and time.monotonic() - memory.attempted_at < source.fresh_s
        if fresh or image is not None:
            self._memory.move_to_end(source)
            on_image(image)
        if fresh:
            return
        self._remember(source).attempted_at = time.monotonic()
        self._reading = source
        self._on_image = on_image
        self._latest.submit(
            self._scope,
            "thumbnail",
            partial(_read, source),
            on_result=partial(self._finish, source),
            on_error=lambda error: self._finish(source, None),
        )

    def cleanup(self) -> None:
        """Stop reading and delivering."""
        self._latest.close()
        self._scope.close()
        self._reading = self._on_image = None

    def _remember(self, source: ThumbnailSource) -> _Memory:
        """Return what is known of *source*, as the most recently used, forgetting the least recently used."""
        memory = self._memory.setdefault(source, _Memory(time.monotonic()))
        self._memory.move_to_end(source)
        while len(self._memory) > REMEMBERED_IMAGES:
            self._memory.popitem(last=False)
        return memory

    def _finish(self, source: ThumbnailSource, image: QImage | None) -> None:
        """Remember what was read, then hand it to whoever last asked."""
        self._remember(source).image = image
        on_image = self._on_image
        self._reading = self._on_image = None
        if on_image is not None:
            on_image(image)
