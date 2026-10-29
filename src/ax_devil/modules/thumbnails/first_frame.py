"""The first frame of a video file or image sequence, kept on disk until the file changes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import av
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage, QImageWriter

from ax_devil.core.atomic_file import write_atomically
from ax_devil.core.cancellation import CancelToken
from ax_devil.core.source_paths import Fingerprint
from ax_devil.modules.cache import CacheManager
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import ImageSequenceConfig
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.thumbnails.scaling import THUMBNAIL_EDGE_PX

logger = get_logger(__name__)

DISK_CACHE_FILES = 500
"""Thumbnails kept on disk (about 20 MB); the oldest are deleted first."""
JPEG_QUALITY = 85


def cached_first_frame(path: Path, image_sequence: ImageSequenceConfig | None, token: CancelToken) -> QImage:
    """Return the first frame of *path* from the disk cache, decoding and caching it when the source changed.

    An image sequence's *path* is a numbered pattern, so its first image file is what is fingerprinted. Raises
    ``FileNotFoundError`` when the source is missing.
    """
    source = _first_image(path, image_sequence) if image_sequence is not None else path
    fingerprint = Fingerprint.of(source)
    if fingerprint is None:
        raise FileNotFoundError(source)
    key = hashlib.sha256(f"{tuple(fingerprint)}|{THUMBNAIL_EDGE_PX}|{image_sequence}".encode()).hexdigest()
    cache_path = CacheManager().get_cache_path("thumbnails", path, ".jpg", content_hash=key)
    try:
        image = QImage.fromData(cache_path.read_bytes())
        if not image.isNull():
            os.utime(cache_path)  # Marks it recently used for keep_newest.
            return image
    except OSError:
        pass
    token.raise_if_cancelled()
    image = read_first_frame(path, image_sequence, token)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QImageWriter(buffer, b"JPEG")
    writer.setQuality(JPEG_QUALITY)
    if writer.write(image):
        try:
            with write_atomically(cache_path, "wb") as file:
                file.write(buffer.data().data())
            keep_newest(cache_path.parent, DISK_CACHE_FILES)
        except OSError as error:
            logger.debug(f"Thumbnail not cached → {path.name}: {error}")
    return image


def _first_image(pattern: Path, image_sequence: ImageSequenceConfig) -> Path:
    """Return the file the numbered *pattern* names at the sequence's start number; raise when it names none."""
    try:
        return Path(str(pattern) % image_sequence.start_number)
    except (TypeError, ValueError) as error:
        raise FileNotFoundError(pattern) from error


def read_first_frame(path: Path, image_sequence: ImageSequenceConfig | None, token: CancelToken) -> QImage:
    """Decode the first frame of *path*, fitted to the thumbnail size; raise when there is none.

    Opens the container directly: the player's reader would index the whole file first.
    """
    config = image_sequence.to_video_open_config() if image_sequence is not None else None
    token.raise_if_cancelled()
    with av.open(
        str(path),
        format=config.av_format if config else None,
        options=config.av_options if config else None,
    ) as container:
        token.raise_if_cancelled()
        frame = next(container.decode(container.streams.video[0]))
        token.raise_if_cancelled()
        scale = min(1.0, THUMBNAIL_EDGE_PX / max(frame.width, frame.height))
        rgb = frame.reformat(
            max(1, round(frame.width * scale)),
            max(1, round(frame.height * scale)),
            format="rgb24",
            interpolation="AREA",
        )
        plane = rgb.planes[0]
        return QImage(bytes(plane), rgb.width, rgb.height, plane.line_size, QImage.Format.Format_RGB888).copy()


def keep_newest(folder: Path, count: int) -> None:
    """Delete the files in *folder* beyond the *count* most recently modified; ignore files that vanish."""
    modified: list[tuple[float, Path]] = []
    try:
        for entry in os.scandir(folder):
            try:
                modified.append((entry.stat().st_mtime, Path(entry.path)))
            except OSError:
                continue
    except OSError:
        return
    modified.sort(reverse=True)
    for _, path in modified[count:]:
        try:
            path.unlink()
        except OSError:
            continue
