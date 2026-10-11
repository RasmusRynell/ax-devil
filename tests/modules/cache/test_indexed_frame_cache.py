"""Binary cache format and independent frame access regressions."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ax_devil.modules.cache import CacheManager, IndexedFrameCache


@pytest.mark.parametrize("use_mmap", [False, True])
@pytest.mark.parametrize("compress", [False, True])
def test_binary_frames_support_direct_access(tmp_path: Path, use_mmap: bool, compress: bool) -> None:
    """Read a later binary frame without decoding an earlier, damaged payload."""
    cache_path = tmp_path / "test.frames"
    frames = {0: b"first\nframe\x00", 3041: bytes(range(256)), 9000: b"last\nframe"}
    with patch.object(CacheManager, "get_cache_path", return_value=cache_path):
        cache = IndexedFrameCache(tmp_path / "source", use_mmap=use_mmap, compress=compress)
        cache.save(frames, meta={"example": "metadata"})
        header_bytes, payload = cache_path.read_bytes().split(b"\n", 1)
        first_length = json.loads(header_bytes)["frame_lengths"]["0"]
        for frame_id in (9000, 0, 3041):
            assert cache.load_frame(frame_id) == frames[frame_id]
        cache.close()

        # Zero the first frame's payload: a later frame must load without decoding it.
        cache_path.write_bytes(header_bytes + b"\n" + bytes(first_length) + payload[first_length:])
        # A reopened reader also honors the persisted compression flag.
        reader = IndexedFrameCache(tmp_path / "source", use_mmap=use_mmap, compress=not compress)
        try:
            assert reader.load_frame(3041) == frames[3041]
            assert reader.load_metadata() == {"example": "metadata"}
            assert reader.available_frames() == set(frames)
            assert reader.load_frame(1234) is None
        finally:
            reader.close()


@pytest.mark.parametrize("use_mmap", [False, True])
def test_open_cache_reads_survive_path_removal(tmp_path: Path, use_mmap: bool) -> None:
    """An open reader owns its handle until close, independently of the file path."""
    cache_path = tmp_path / "test.frames"
    with patch.object(CacheManager, "get_cache_path", return_value=cache_path):
        cache = IndexedFrameCache(tmp_path / "source", use_mmap=use_mmap)
        try:
            assert cache.load_frame(0) is None
            cache.save({0: "first", 3041: "later"})
            assert cache.load_frame(0) == "first"
            cache_path.unlink()
            assert cache.load_frame(3041) == "later"
            cache.close()
            assert cache.load_frame(3041) is None
        finally:
            cache.close()
