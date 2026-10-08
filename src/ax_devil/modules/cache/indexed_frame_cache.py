from __future__ import annotations

import gzip
import json
import mmap
import pickle
from pathlib import Path
from typing import Any, BinaryIO

from ax_devil.modules.settings.logging_config import get_logger

from .cache_manager import CacheManager

logger = get_logger(__name__)
DEFAULT_PROTOCOL = pickle.HIGHEST_PROTOCOL


class IndexedFrameCache:
    """
    Single-file cache with byte-offset index, optional gzip, and mmap.

    Layout
    ------
    header-json\n
    frame-0-bytes
    frame-1-bytes
    …

    • Offsets in the header are relative to the *first* payload byte
      (right after the header newline).
    • Payloads are binary pickle with optional independent gzip compression.
      Indexed lengths delimit frames, including payloads containing newlines.
    """

    # ------------------------------------------------------------------ #
    # construction                                                       #
    # ------------------------------------------------------------------ #

    def __init__(
        self,
        source_path: str | Path,
        *,
        cache_suffix: str = ".frames",
        cache_type: str = "scene",
        use_mmap: bool = True,
        compress: bool = True,
    ) -> None:
        self.source_path = Path(source_path)

        # Use CacheManager for unified cache path generation
        self._cache_type = str(cache_type)
        cache_manager = CacheManager()
        self.cache_file = cache_manager.get_cache_path(
            cache_type=self._cache_type, source_path=self.source_path, suffix=cache_suffix
        )

        self._use_mmap_cfg = bool(use_mmap)
        self._compress_cfg = bool(compress)

        # populated lazily on first read
        self._header: dict[str, Any] | None = None
        self._payload0: int | None = None  # abs. byte offset of first frame
        self._mm: mmap.mmap | None = None  # active mmap
        self._mmap_fh: BinaryIO | None = None  # file handle kept alive for mmap
        self._fh: BinaryIO | None = None  # fallback handle when mmap off

    # ------------------------------------------------------------------ #
    # helpers                                                            #
    # ------------------------------------------------------------------ #

    # -- (en|de)coding ---------------------------------------------------

    def _encode_frame(self, obj: Any) -> bytes:
        data = pickle.dumps(obj, protocol=DEFAULT_PROTOCOL)
        if self._compress_cfg:
            data = gzip.compress(data, compresslevel=1)
        return data

    def _decode_frame(self, payload: bytes) -> Any:
        data = payload
        if self._compress_cfg:
            data = gzip.decompress(data)
        return pickle.loads(data)

    # -- header / handle cache ------------------------------------------

    def _ensure_open(self) -> None:
        """Open file, parse header, set up mmap - exactly once."""
        if self._header is not None:
            return
        if not self.exists():
            raise FileNotFoundError(self.cache_file)

        if self._use_mmap_cfg:
            self._mmap_fh = self.cache_file.open("rb")
            self._mm = mmap.mmap(self._mmap_fh.fileno(), 0, access=mmap.ACCESS_READ)
            newline = self._mm.find(b"\n")
            if newline == -1:
                raise RuntimeError("corrupt cache: missing header newline")
            header_bytes = self._mm[:newline]
            self._payload0 = newline + 1
        else:
            self._fh = self.cache_file.open("rb")
            header_bytes = self._fh.readline().rstrip(b"\n")
            self._payload0 = self._fh.tell()

        self._header = json.loads(header_bytes.decode("utf-8"))
        # honour file's compress flag (overrides ctor)
        self._compress_cfg = bool(self._header.get("compress", False))

        logger.debug(
            f"Opened cache {self.cache_file.name} | frames={self._header['frame_count']} | "
            f"mmap={self._use_mmap_cfg} | compress={self._compress_cfg}"
        )

    # ------------------------------------------------------------------ #
    # public API                                                         #
    # ------------------------------------------------------------------ #

    def exists(self) -> bool:
        """True iff the cache file exists."""
        return self.cache_file.exists()

    # -- write -----------------------------------------------------------

    def save(
        self,
        frames: dict[int, Any],
        *,
        meta: dict[str, Any] | None = None,
        overwrite: bool = True,
    ) -> None:
        """
        Overwrite the cache with *frames*.

        frames : {frame_number: python_object}
        meta   : arbitrary JSON-serialisable dict
        """
        if self.exists() and not overwrite:
            raise FileExistsError(self.cache_file)

        items = sorted(frames.items())  # deterministic order
        frame_to_byte: dict[str, int] = {}
        frame_lengths: dict[str, int] = {}
        encoded_frames: list[bytes] = []

        offset = 0  # bytes after header newline
        for fn, obj in items:
            enc = self._encode_frame(obj)
            encoded_frames.append(enc)
            key = str(fn)
            frame_to_byte[key] = offset
            frame_lengths[key] = len(enc)
            offset += len(enc)

        header = {
            "frame_count": len(items),
            "frame_numbers": [fn for fn, _ in items],
            "frame_to_byte": frame_to_byte,
            "frame_lengths": frame_lengths,
            "metadata": meta or {},
            "compress": self._compress_cfg,
        }
        header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")

        with self.cache_file.open("wb") as fh:
            fh.write(header_bytes + b"\n")
            for enc in encoded_frames:
                fh.write(enc)

        logger.debug(f"Cached {len(items)} frames ({self.cache_file.stat().st_size} bytes) → {self.cache_file.name}")

        self.close()  # purge any stale handles/header

    # -- read ------------------------------------------------------------

    def load_frame(self, frame_number: int) -> Any | None:
        """Return the requested frame or *None* if not cached."""
        try:
            self._ensure_open()
            hdr = self._header
            assert hdr is not None

            key = str(frame_number)
            byte_rel = hdr["frame_to_byte"].get(key)
            if byte_rel is None:
                logger.debug(f"frame {frame_number} not found in {self.cache_file.name}")
                return None

            assert self._payload0 is not None
            abs_start = self._payload0 + int(byte_rel)
            length = int(hdr["frame_lengths"][key])

            if self._mm is not None:
                payload = self._mm[abs_start : abs_start + length]
            else:
                assert self._fh is not None
                self._fh.seek(abs_start)
                payload = self._fh.read(length)

            return self._decode_frame(payload)
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.warning(f"Could not load frame {frame_number} from cache: {exc}")
            return None

    # -- light helpers ---------------------------------------------------

    def available_frames(self) -> set[int]:
        """Set of all cached frame numbers (empty if cache missing)."""
        if not self.exists():
            return set()
        try:
            self._ensure_open()
            assert self._header is not None
            return {int(x) for x in self._header["frame_numbers"]}
        except Exception:
            return set()

    def load_metadata(self) -> dict[str, Any] | None:
        """Metadata dict recorded at save-time (or *None* if cache missing)."""
        if not self.exists():
            return None
        try:
            self._ensure_open()
            assert self._header is not None
            meta = self._header.get("metadata", {})
            return meta if isinstance(meta, dict) else None
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # clean-up                                                           #
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """Explicitly free mmap / file handles and forget the header."""
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._mmap_fh is not None:
            self._mmap_fh.close()
            self._mmap_fh = None
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._header = None
        self._payload0 = None
        logger.debug(f"IndexedFrameCache closed: {self.cache_file.name}")

    # context-manager sugar ---------------------------------------------

    def __enter__(self) -> "IndexedFrameCache":  # noqa: D401
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:  # noqa: D401
        self.close()

    def __del__(self) -> None:
        try:
            # Ensure handles are closed on GC as a last resort
            self.close()
        except Exception:
            logger.debug(f"IndexedFrameCache id={id(self)} failed to close on garbage collection", exc_info=True)
