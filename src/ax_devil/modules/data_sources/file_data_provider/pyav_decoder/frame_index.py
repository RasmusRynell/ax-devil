"""Frame indexing module for reliable frame-based video navigation."""

import hashlib
import re
import struct
import tempfile
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any

import av

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import VideoOpenConfig
from ax_devil.modules.data_sources.timing_reports import VideoTimingProfile
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

_CACHE_MAGIC = b"AXDVPTS"
_CACHE_VERSION = 3
_HEADER_FMT = "<7sBqqQQQQ"  # magic + version + time_base + inode + size + mtime_ns + frame_count
_STRUCT_FMT = "<qB"  # int64 pts + uint8 key
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_STRUCT_SIZE = struct.calcsize(_STRUCT_FMT)


class FrameIndex:
    """Frame index that maps frame numbers to PTS values and tracks keyframes."""

    def __init__(
        self,
        video_path: Path,
        cache_path: Path | None = None,
        open_config: VideoOpenConfig | None = None,
    ) -> None:
        self.video_path = video_path
        self.cache_path = cache_path
        self._open_config = open_config
        self.time_base: Fraction | None = None
        self.pts_list: list[int] = []
        self.is_key_list: list[bool] = []
        self.pts_to_index: dict[int, int] = {}
        self._keyframe_indices: list[int] | None = None

    def _fingerprint(self) -> tuple[int, int, int]:
        if self.video_path.exists():
            return self._file_fingerprint()
        if self._open_config is not None and self._open_config.av_format == "image2":
            return self._image_sequence_fingerprint()

        return self._file_fingerprint()

    def _file_fingerprint(self) -> tuple[int, int, int]:
        st = self.video_path.stat()
        return (st.st_ino, st.st_size, st.st_mtime_ns)

    def _image_sequence_fingerprint(self) -> tuple[int, int, int]:
        if self._open_config is None:
            raise RuntimeError("Image sequence fingerprinting requires a video open config")

        sequence_files = self._matching_image_sequence_files()
        if not sequence_files:
            raise FileNotFoundError(f"No image sequence files match {self.video_path}")

        open_options = tuple(sorted(self._open_config.av_options.items()))
        descriptor = f"{self.video_path.resolve()}|{self._open_config.av_format}|{open_options}"
        digest = hashlib.blake2b(descriptor.encode("utf-8"), digest_size=8)
        total_size = 0
        latest_mtime_ns = 0

        for image_path in sequence_files:
            stat_result = image_path.stat()
            total_size += stat_result.st_size
            latest_mtime_ns = max(latest_mtime_ns, stat_result.st_mtime_ns)
            digest.update(image_path.name.encode("utf-8"))
            digest.update(str(stat_result.st_size).encode("ascii"))
            digest.update(str(stat_result.st_mtime_ns).encode("ascii"))

        source_hash = int.from_bytes(digest.digest(), byteorder="little", signed=False)
        return (source_hash, total_size, latest_mtime_ns)

    def _matching_image_sequence_files(self) -> list[Path]:
        match = re.search(r"%0?\d*d", self.video_path.name)
        if match is None or not self.video_path.parent.is_dir():
            return []

        filename_regex = re.compile(
            f"^{re.escape(self.video_path.name[: match.start()])}"
            r"(\d+)"
            f"{re.escape(self.video_path.name[match.end() :])}$"
        )
        numbered_files: list[tuple[int, Path]] = []
        for candidate in self.video_path.parent.iterdir():
            if not candidate.is_file():
                continue
            number_match = filename_regex.match(candidate.name)
            if number_match is None:
                continue
            numbered_files.append((int(number_match.group(1)), candidate))

        return [candidate for _, candidate in sorted(numbered_files)]

    def _validate_frame_index(self, frame_index: int) -> None:
        """Validate frame index is within bounds."""
        if not (0 <= frame_index < len(self.pts_list)):
            raise IndexError(f"Frame index {frame_index} out of bounds (0-{len(self.pts_list) - 1})")

    def build(self) -> None:
        """Build frame index in decoded presentation order.

        Packet order can differ from display order for videos with B-frames or
        reordered timestamps. Frame numbers in the app must follow decoded
        presentation order because that is what users see during playback.
        """
        logger.debug(f"Building frame index by decoded frame scan → {self.video_path.name}")

        self.pts_list.clear()
        self.is_key_list.clear()
        self.time_base = None

        fmt = self._open_config.av_format if self._open_config else None
        opts = self._open_config.av_options if self._open_config else None
        with av.open(str(self.video_path), mode="r", format=fmt, options=opts) as container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            self.time_base = Fraction(stream.time_base) if stream.time_base is not None else None
            for frame in container.decode(stream):
                if frame.pts is None:
                    continue
                self.pts_list.append(frame.pts)
                self.is_key_list.append(bool(getattr(frame, "key_frame", False)))

        self.pts_to_index = {pts: i for i, pts in enumerate(self.pts_list)}
        self._keyframe_indices = None  # Reset cache
        logger.debug(f"Built frame index via decoded frame scan: {len(self.pts_list)} frames")

    def save_to_file(self) -> None:
        """Save frame index to cache file (if cache_path provided)."""
        if not self.pts_list:
            logger.warning("Cannot save empty frame index")
            return

        if self.cache_path is None:
            logger.debug("No cache path provided, skipping frame index save")
            return

        if self.time_base is None:
            logger.warning("Cannot save frame index without video time base")
            return

        # Ensure cache directory exists
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        temporary_path: Path | None = None
        try:
            inode, size, mtime_ns = self._fingerprint()
            with tempfile.NamedTemporaryFile(dir=self.cache_path.parent, delete=False) as fh:
                temporary_path = Path(fh.name)
                fh.write(
                    struct.pack(
                        _HEADER_FMT,
                        _CACHE_MAGIC,
                        _CACHE_VERSION,
                        self.time_base.numerator,
                        self.time_base.denominator,
                        inode,
                        size,
                        mtime_ns,
                        len(self.pts_list),
                    )
                )
                for pts, is_key in zip(self.pts_list, self.is_key_list, strict=True):
                    fh.write(struct.pack(_STRUCT_FMT, pts, is_key))
            temporary_path.replace(self.cache_path)
            logger.debug(f"Frame index written to cache → {self.cache_path.name}")
        except OSError as e:
            logger.error(f"Failed to write cache file {self.cache_path.name}: {e}")
            raise
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def load_from_file(self) -> bool:
        """Load frame index from cache file (if cache_path provided). Returns True if successful."""
        if self.cache_path is None:
            logger.debug("No cache path provided, skipping frame index load")
            return False

        if not self.cache_path.exists():
            logger.debug(f"Cache file does not exist: {self.cache_path.name}")
            return False

        self.pts_list.clear()
        self.is_key_list.clear()
        self.time_base = None
        self.pts_to_index.clear()
        self._keyframe_indices = None

        try:
            with self.cache_path.open("rb") as fh:
                header = fh.read(_HEADER_SIZE)
                if len(header) != _HEADER_SIZE:
                    logger.warning(f"Frame index cache has no valid header: {self.cache_path.name}")
                    return False

                magic, version, numerator, denominator, inode, size, mtime_ns, frame_count = struct.unpack(
                    _HEADER_FMT, header
                )
                if magic != _CACHE_MAGIC or version != _CACHE_VERSION:
                    logger.warning(f"Unsupported frame index cache version: {self.cache_path.name}")
                    return False

                if denominator == 0:
                    logger.warning(f"Frame index cache has invalid time base: {self.cache_path.name}")
                    return False

                if (inode, size, mtime_ns) != self._fingerprint():
                    logger.debug(f"Frame index cache fingerprint mismatch: {self.cache_path.name}")
                    return False

                records = fh.read()
                if frame_count == 0 or len(records) != frame_count * _STRUCT_SIZE:
                    logger.warning(f"Incomplete frame index cache: {self.cache_path.name}")
                    return False

            self.time_base = Fraction(numerator, denominator)
            for pts, is_key in struct.iter_unpack(_STRUCT_FMT, records):
                self.pts_list.append(pts)
                self.is_key_list.append(bool(is_key))

        except (OSError, struct.error) as e:
            logger.error(f"Failed to load cache file {self.cache_path.name}: {e}")
            self.pts_list.clear()
            self.is_key_list.clear()
            self.time_base = None
            return False

        self.pts_to_index = {pts: i for i, pts in enumerate(self.pts_list)}
        self._keyframe_indices = None  # Reset cache
        logger.debug(f"Loaded frame index from cache ← {self.cache_path.name}")
        return True

    def get_frame_pts(self, frame_index: int) -> int:
        """Get PTS for a given frame index."""
        self._validate_frame_index(frame_index)
        return self.pts_list[frame_index]

    def pts_to_seconds(self, pts: int) -> float:
        """Convert raw PTS to seconds using the video stream time base."""
        if self.time_base is None:
            raise ValueError(f"Frame index has no time base for {self.video_path}")
        return float(pts * self.time_base)

    def get_frame_time_us(self, frame_index: int) -> float:
        """Get frame presentation time in microseconds since the first indexed frame."""
        pts = self.get_frame_pts(frame_index)
        first_pts = self.pts_list[0]
        return self.pts_to_seconds(pts - first_pts) * 1_000_000.0

    def get_frame_times_us(self) -> tuple[int, ...]:
        """Return all frame presentation times in microseconds since the first indexed frame."""
        if not self.pts_list:
            return ()
        first_pts = self.pts_list[0]
        return tuple(int(self.pts_to_seconds(pts - first_pts) * 1_000_000.0) for pts in self.pts_list)

    def get_frame_period_after_s(self, frame_index: int) -> float | None:
        """Get presentation delay from this frame to the next frame, or nearest valid fallback."""
        self._validate_frame_index(frame_index)
        pts_delta: int | None = None
        if frame_index < len(self.pts_list) - 1:
            pts_delta = self.pts_list[frame_index + 1] - self.pts_list[frame_index]
        elif frame_index > 0:
            pts_delta = self.pts_list[frame_index] - self.pts_list[frame_index - 1]

        if pts_delta is None or pts_delta <= 0:
            return None
        return self.pts_to_seconds(pts_delta)

    def get_frame_source_timing_metadata(self, frame_index: int) -> dict[str, Any]:
        """Return raw source timing facts for debug display."""
        self._validate_frame_index(frame_index)
        pts = self.pts_list[frame_index]
        first_pts = self.pts_list[0]
        metadata: dict[str, Any] = {
            "video_timestamp_source": "pts_time_base_minus_first_pts",
            "video_pts": pts,
            "video_first_pts": first_pts,
            "video_pts_delta": pts - first_pts,
        }
        if self.time_base is not None:
            metadata.update(
                {
                    "video_time_base": f"{self.time_base.numerator}/{self.time_base.denominator}",
                    "video_time_base_numerator": self.time_base.numerator,
                    "video_time_base_denominator": self.time_base.denominator,
                }
            )
        return metadata

    def analyze_timing_profile(self) -> VideoTimingProfile:
        """Analyze decoded PTS cadence for the indexed video."""
        total_frames = len(self.pts_list)
        periods_us: list[int] = []

        for frame_index in range(max(0, total_frames - 1)):
            period_s = self.get_frame_period_after_s(frame_index)
            if period_s is not None and period_s > 0:
                periods_us.append(round(period_s * 1_000_000.0))

        return VideoTimingProfile(
            total_frames=total_frames,
            is_variable_cadence=len(set(periods_us)) > 1,
            period_modes_us=tuple(Counter(periods_us).most_common(5)),
        )

    def _build_keyframe_cache(self) -> None:
        """Build cached list of keyframe indices for faster searching."""
        if self._keyframe_indices is None:
            self._keyframe_indices = [i for i, is_key in enumerate(self.is_key_list) if is_key]

    def get_nearest_keyframe_before(self, frame_index: int) -> int:
        """Find the nearest keyframe at or before the given frame index."""
        self._validate_frame_index(frame_index)
        self._build_keyframe_cache()

        if not self._keyframe_indices:
            return 0

        # Binary search for the nearest keyframe at or before frame_index
        left, right = 0, len(self._keyframe_indices) - 1
        result = 0

        while left <= right:
            mid = (left + right) // 2
            keyframe_idx = self._keyframe_indices[mid]

            if keyframe_idx <= frame_index:
                result = keyframe_idx
                left = mid + 1
            else:
                right = mid - 1

        return result

    def get_total_frames(self) -> int:
        """Get total number of frames in the index."""
        return len(self.pts_list)

    def is_keyframe(self, frame_index: int) -> bool:
        """Check if the given frame index is a keyframe."""
        if not (0 <= frame_index < len(self.is_key_list)):
            return False
        return self.is_key_list[frame_index]
