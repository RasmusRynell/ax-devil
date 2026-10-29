"""Thumbnails of video content for hover cards."""

from ax_devil.modules.thumbnails.scaling import THUMBNAIL_EDGE_PX, fit
from ax_devil.modules.thumbnails.sources import (
    SNAPSHOT_TIMEOUT_S,
    CameraThumbnail,
    FileThumbnail,
    OpenFrames,
    ThumbnailSource,
)
from ax_devil.modules.thumbnails.thumbnails import Thumbnails

__all__ = [
    "SNAPSHOT_TIMEOUT_S",
    "THUMBNAIL_EDGE_PX",
    "CameraThumbnail",
    "FileThumbnail",
    "OpenFrames",
    "ThumbnailSource",
    "Thumbnails",
    "fit",
]
