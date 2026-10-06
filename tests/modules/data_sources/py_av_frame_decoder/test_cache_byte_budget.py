"""Byte reservations preserve frame delivery and useful prefetch under small budgets."""

from pathlib import Path
from unittest.mock import patch

import av
import numpy as np
import pytest

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.cached_frame import CachedFrame
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache import (
    FrameCache,
    get_frame_cache_registry,
)
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_frame_reader import FrameReaderWorker
from tests.helpers.video import create_test_video, make_cached_frame


def test_mixed_sizes_replacement_and_oversized_admission() -> None:
    """Evict by bytes, account replacements and clear without retaining oversized entries."""
    cache = FrameCache(4 * 768)
    small = make_cached_frame()
    large = make_cached_frame(2, width=32)
    assert cache.put(0, small)
    assert cache.put(1, make_cached_frame(1))
    assert cache.put(2, large)
    assert cache.get(0) is small  # Frame 1 becomes the eviction candidate.
    assert cache.put(3, make_cached_frame(3))
    assert not cache.contains(1)
    assert cache.contains(0)
    assert cache.put(2, make_cached_frame(2))
    snapshot = get_frame_cache_registry().snapshot_details()[str(id(cache))]
    assert snapshot["reserved_bytes"] == 3 * 768
    assert not cache.put(4, make_cached_frame(4, width=128))
    assert cache.size() == 3
    cache.clear()
    snapshot = get_frame_cache_registry().snapshot_details()[str(id(cache))]
    assert snapshot["reserved_bytes"] == 0
    assert snapshot["ranges"] == ()


@pytest.mark.parametrize("pixel_format", ["yuv420p", "yuv444p10le", "rgb24"])
def test_reservation_covers_source_planes_and_converted_rgb(pixel_format: str) -> None:
    """Pixel format and plane padding cannot make conversion exceed its reservation."""
    raw = av.VideoFrame(18, 16, pixel_format)
    for plane in raw.planes:
        plane.update(bytes(plane.buffer_size))
    frame = CachedFrame(frame_index=0, video_frame=raw, timestamp_us=0, period_after_s=None, source_timing_metadata={})
    reservation = frame.reserved_bytes
    assert reservation >= sum(plane.buffer_size for plane in raw.planes)
    cache = FrameCache(reservation)
    assert cache.put(0, frame)
    decoded = frame.to_decoded_frame()
    assert reservation >= decoded.pixels.nbytes
    assert frame.reserved_bytes == reservation
    assert get_frame_cache_registry().snapshot_details()[str(id(cache))]["reserved_bytes"] == reservation


@pytest.mark.parametrize("retained_frames", [0, 1, 3])
def test_small_budget_forward_backward_and_prefetch(tmp_path: Path, retained_frames: int) -> None:
    """Full prefetch windows still advance, and uncached frames remain correct."""
    path = tmp_path / "video.mp4"
    create_test_video(path)
    with av.open(str(path)) as container:
        reference = [frame.to_ndarray(format="rgb24") for frame in container.decode(video=0)]
    frame_bytes = reference[0].nbytes
    worker = FrameReaderWorker(str(path), max(1, retained_frames * frame_bytes), prefetch_count=50)
    try:
        with patch.object(worker._frame_processor, "jump_to", wraps=worker._frame_processor.jump_to) as jump:
            for index in range(20):
                frame = worker.read_decoded_frame(index)
                assert frame is not None
                np.testing.assert_array_equal(frame.pixels, reference[index])
                assert frame.frame_index == index
                assert frame.timestamp_us == pytest.approx(index * 1_000_000 / 30)
                cached = worker._cache.get(index)
                if retained_frames:
                    assert cached is not None and cached.is_converted
                    assert cached.to_decoded_frame().pixels is frame.pixels
                for _ in range(51):
                    if not worker.prefetch_one():
                        break
                else:
                    pytest.fail("Prefetch failed to stop at its budget or horizon")
                if retained_frames:
                    assert worker._cache.contains(index)
                snapshot = get_frame_cache_registry().snapshot_details()[str(id(worker._cache))]
                assert snapshot["reserved_bytes"] <= max(1, retained_frames * frame_bytes)
            assert jump.call_count == 1, "Forward playback should not repeatedly seek when the cache fills"
        for index in (5, 4, 3, 25, 0, 29):
            frame = worker.read_decoded_frame(index)
            assert frame is not None
            np.testing.assert_array_equal(frame.pixels, reference[index])
            assert frame.frame_index == index
    finally:
        worker.cleanup()


def test_prefetch_protects_nearer_frames_but_can_evict_history() -> None:
    """A full cache can advance without throwing away its next-needed frames."""
    cache = FrameCache(3 * 768)
    for index in range(3):
        assert cache.put(index, make_cached_frame(index))
    assert not cache.put(3, make_cached_frame(3), current_frame=0)
    assert all(cache.contains(index) for index in range(3))
    assert cache.put(3, make_cached_frame(3), current_frame=1)
    assert not cache.contains(0)
    assert all(cache.contains(index) for index in (1, 2, 3))


def test_eviction_during_lookup_still_delivers_the_requested_frame(tmp_path: Path) -> None:
    """An external resize racing a cached read must become a miss, not a missing frame."""
    path = tmp_path / "video.mp4"
    create_test_video(path)
    worker = FrameReaderWorker(str(path), 1024**2, prefetch_count=50)
    try:
        expected = worker.read_decoded_frame(0)
        assert expected is not None
        assert worker._cache.contains(0)
        original_get = worker._cache.get

        def evict_before_lookup(index: int) -> CachedFrame | None:
            worker._cache.set_budget_bytes(0)
            return original_get(index)

        with patch.object(worker._cache, "get", side_effect=evict_before_lookup):
            actual = worker.read_decoded_frame(0)
        assert actual is not None
        assert actual.frame_index == expected.frame_index
        assert actual.timestamp_us == expected.timestamp_us
        np.testing.assert_array_equal(actual.pixels, expected.pixels)
    finally:
        worker.cleanup()
