"""Byte reservations preserve frame delivery and useful prefetch under small budgets."""

from collections.abc import Sequence
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
            assert jump.call_count <= 1, "Forward playback should not repeatedly seek when the cache fills"
        for index in (5, 4, 3, 25, 0, 29):
            frame = worker.read_decoded_frame(index)
            assert frame is not None
            np.testing.assert_array_equal(frame.pixels, reference[index])
            assert frame.frame_index == index
    finally:
        worker.cleanup()


def test_prefetch_protects_kept_frames_but_can_evict_history() -> None:
    """A full cache can advance without throwing away its next-needed frames."""
    cache = FrameCache(3 * 768)
    for index in range(3):
        assert cache.put(index, make_cached_frame(index))
    assert not cache.put(3, make_cached_frame(3), keep=range(0, 4))
    assert all(cache.contains(index) for index in range(3))
    assert cache.put(3, make_cached_frame(3), keep=range(1, 4))
    assert not cache.contains(0)
    assert all(cache.contains(index) for index in (1, 2, 3))


@pytest.mark.parametrize(
    ("reads", "playhead"),
    [((40, 10), 40), ((10, 40), 10)],
    ids=["decoder-behind-playhead", "decoder-ahead-of-playhead"],
)
def test_prefetch_fills_the_window_after_the_playhead_wherever_the_decoder_is(
    tmp_path: Path, reads: tuple[int, ...], playhead: int
) -> None:
    """A cache hit away from the decoder still fills the frames after the playhead, and nothing else."""
    path = tmp_path / "video.mp4"
    create_test_video(path, duration=3.0, gop=10)
    worker = FrameReaderWorker(str(path), 1024**3, prefetch_count=5)
    try:
        for index in reads:
            assert worker.read_decoded_frame(index) is not None
        assert worker.read_decoded_frame(playhead) is not None  # Cache hit, decoder elsewhere.
        before = set(_cached_frames(worker))
        for _ in range(100):
            if not worker.prefetch_one():
                break
        else:
            pytest.fail("Prefetch did not stop after filling its window")
        window = set(range(playhead + 1, playhead + 5))
        assert window <= set(_cached_frames(worker))
        keyframe_history = set(range(playhead - playhead % 10, playhead))
        assert set(_cached_frames(worker)) - before <= window | keyframe_history
    finally:
        worker.cleanup()


def test_a_read_decodes_from_its_keyframe_when_the_decoder_is_in_an_earlier_group(tmp_path: Path) -> None:
    """A read near the playhead does not decode on from a decoder parked groups earlier."""
    path = tmp_path / "video.mp4"
    create_test_video(path, duration=3.0, gop=10)
    worker = FrameReaderWorker(str(path), 1024**3, prefetch_count=50)
    try:
        assert worker.read_decoded_frame(10) is not None  # Decoder parks at 11.
        with patch.object(worker._frame_processor, "jump_to", wraps=worker._frame_processor.jump_to) as jump:
            assert worker.read_decoded_frame(45) is not None
        jump.assert_called_once_with(45)
        assert not worker._cache.contains(20), "Read decoded on through earlier groups"
    finally:
        worker.cleanup()


def test_prefetch_under_a_small_budget_decodes_each_keyframe_group_once(tmp_path: Path) -> None:
    """Frames a seek returns together never evict each other, so the group is not decoded again."""
    path = tmp_path / "video.mp4"
    create_test_video(path, duration=6.0, gop=10)
    with av.open(str(path)) as container:
        frame_bytes = next(container.decode(video=0)).to_ndarray(format="rgb24").nbytes
    worker = FrameReaderWorker(str(path), 5 * frame_bytes, prefetch_count=5)
    try:
        for index in range(171):
            assert worker.read_decoded_frame(index) is not None
        assert worker.read_decoded_frame(20) is not None
        assert worker.read_decoded_frame(170) is not None  # Cache hit, decoder in an earlier group.
        with patch.object(worker._frame_processor, "jump_to", wraps=worker._frame_processor.jump_to) as jump:
            for _ in range(100):
                if not worker.prefetch_one():
                    break
            else:
                pytest.fail("Prefetch did not stop")
        assert jump.call_count <= 1
        assert all(worker._cache.contains(index) for index in range(170, 175))
    finally:
        worker.cleanup()


@pytest.mark.parametrize(
    ("reads", "filled", "untouched"),
    [
        ((60, 59, 58, 52), range(43, 52), range(61, 70)),
        ((60, 52), range(53, 62), range(43, 50)),
        ((60, 59, 58, 52, 53), range(54, 63), range(43, 50)),
    ],
    ids=["three-steps-back-fill-behind", "one-step-back-fills-ahead", "forward-step-fills-ahead-again"],
)
def test_repeated_backward_steps_prefetch_behind_the_playhead(
    tmp_path: Path, reads: tuple[int, ...], filled: range, untouched: range
) -> None:
    """Three short steps back fill the frames behind the playhead by keyframe group; a forward step stops it."""
    path = tmp_path / "video.mp4"
    create_test_video(path, duration=3.0, gop=10)
    worker = FrameReaderWorker(str(path), 1024**3, prefetch_count=10)
    try:
        for index in reads:
            assert worker.read_decoded_frame(index) is not None
        for _ in range(100):
            if not worker.prefetch_one():
                break
        else:
            pytest.fail("Prefetch did not stop after filling its window")
        cached = set(_cached_frames(worker))
        assert set(filled) <= cached
        assert not set(untouched) & cached
    finally:
        worker.cleanup()


def test_a_read_during_a_backward_fill_reads_on_from_where_the_fill_stopped(tmp_path: Path) -> None:
    """Stepping back before the fill reaches the frame keeps the fill's progress instead of seeking again."""
    path = tmp_path / "video.mp4"
    create_test_video(path, duration=3.0, gop=30)
    worker = FrameReaderWorker(str(path), 1024**3, prefetch_count=10)
    try:
        for index in (89, 80, 72, 64):
            assert worker.read_decoded_frame(index) is not None
        for _ in range(15):
            assert worker.prefetch_one()  # Interrupted partway through the 30..59 group.
        with patch.object(worker._frame_processor, "jump_to", wraps=worker._frame_processor.jump_to) as jump:
            assert worker.read_decoded_frame(59) is not None
        jump.assert_not_called()
    finally:
        worker.cleanup()


@pytest.mark.parametrize(
    ("duration", "gop", "budget_frames", "reads"),
    [(4.0, 60, 12, range(110, 80, -1)), (6.0, 10, 8, (179, 178, 177, 176))],
    ids=["group-longer-than-budget", "seek-returns-frames-past-playhead"],
)
def test_backward_prefetch_decodes_each_keyframe_group_once(
    tmp_path: Path, duration: float, gop: int, budget_frames: int, reads: Sequence[int]
) -> None:
    """Walking back under a small budget never decodes a keyframe group again to keep one more frame."""
    path = tmp_path / "video.mp4"
    create_test_video(path, duration=duration, gop=gop)
    worker = FrameReaderWorker(str(path), budget_frames * _frame_bytes(path), prefetch_count=10)
    prefetch_jumps: list[int] = []
    try:
        for index in reads:
            assert worker.read_decoded_frame(index) is not None
            with patch.object(worker._frame_processor, "jump_to", wraps=worker._frame_processor.jump_to) as jump:
                _drain_prefetch(worker)
            prefetch_jumps.extend(call.args[0] for call in jump.call_args_list)
        assert len(prefetch_jumps) == len(set(prefetch_jumps)), f"Repeated keyframe jumps: {prefetch_jumps}"
    finally:
        worker.cleanup()


def test_forward_prefetch_jumps_into_a_group_longer_than_the_budget(tmp_path: Path) -> None:
    """Resuming from a cached frame fills the frames after it even when their keyframe group exceeds the budget."""
    path = tmp_path / "video.mp4"
    create_test_video(path, duration=4.0, gop=60)
    worker = FrameReaderWorker(str(path), 35 * _frame_bytes(path), prefetch_count=10)
    try:
        for index in (100, 10, 100):  # The last read is a cache hit with the decoder parked at 11.
            assert worker.read_decoded_frame(index) is not None
        _drain_prefetch(worker)
        assert all(worker._cache.contains(index) for index in range(101, 110))
    finally:
        worker.cleanup()


def _frame_bytes(path: Path) -> int:
    with av.open(str(path)) as container:
        return int(next(container.decode(video=0)).to_ndarray(format="rgb24").nbytes)


def _drain_prefetch(worker: FrameReaderWorker) -> None:
    for _ in range(100):
        if not worker.prefetch_one():
            return
    pytest.fail("Prefetch did not stop")


def _cached_frames(worker: FrameReaderWorker) -> list[int]:
    return [index for first, last in worker.get_cached_ranges() for index in range(first, last + 1)]


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
