"""Tests for the standalone synchronization engine."""

import pytest

from ax_devil.core.data_types import FrameIdentifier, OverlayData
from ax_devil.modules.scene.model import Scene, TimeSlice
from ax_devil.modules.synchronization import StreamSync, SyncResult
from ax_devil.modules.video_viewer.overlay_persistence import (
    OverlayPersistencePolicy,
    OverlayPersistenceSettings,
    OverlaySelection,
)


@pytest.mark.parametrize("fps", [60, 90])
def test_live_buffer_reports_early_eviction_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, fps: int
) -> None:
    """Production buffering reports starvation without flooding logs or changing output."""
    now = 0.0
    monkeypatch.setattr("ax_devil.modules.synchronization.engine.time.monotonic", lambda: now)
    results: list[SyncResult[int, str]] = []
    sync: StreamSync[int, str] = StreamSync("buffer-limit", delay_ms=1500)
    sync.set_output_callback(results.append)
    try:
        for frame in range(1000):
            now = frame / fps
            sync.push_frame(frame, capture_time=now)

        errors = [record for record in caplog.records if record.levelname == "ERROR"]
        if fps <= 60:
            assert results
            assert errors == []
        else:
            assert results == []
            assert len(errors) == 1
            assert "1500 ms delay, 100 frame capacity" in errors[0].message
            assert "reduce the source frame rate" in errors[0].message
    finally:
        sync.cleanup()


def test_live_buffer_reports_bursts_again_after_reset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Bursts trigger the diagnostic even without advancing capture timestamps or time."""
    monkeypatch.setattr("ax_devil.modules.synchronization.engine.time.monotonic", lambda: 0.0)
    sync: StreamSync[int, str] = StreamSync("burst-limit", delay_ms=1500)
    sync.set_output_callback(lambda _result: None)
    try:
        for _ in range(2):
            for frame in range(200):
                sync.push_frame(frame, capture_time=0.0)
            sync.reset()
        assert len([record for record in caplog.records if record.levelname == "ERROR"]) == 2
    finally:
        sync.cleanup()


def test_live_buffer_does_not_report_eviction_of_ready_frames(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A full queue alone is not evidence that the playback delay exceeds capacity."""
    now = 0.0
    monkeypatch.setattr("ax_devil.modules.synchronization.engine.time.monotonic", lambda: now)
    sync: StreamSync[int, str] = StreamSync("ready-limit", delay_ms=1500, max_queue_size=2)
    sync.set_output_callback(lambda _result: None)
    try:
        sync.push_frame(0, capture_time=0.0)
        sync.push_frame(1, capture_time=0.0)
        now = 1.5
        sync.push_frame(2, capture_time=now)
        assert not [record for record in caplog.records if record.levelname == "ERROR"]
    finally:
        sync.cleanup()


@pytest.mark.parametrize("with_overlays", [False, True])
def test_buffered_frames_release_at_delay_boundary(monkeypatch: pytest.MonkeyPatch, with_overlays: bool) -> None:
    """Inputs release all ready frames at the inclusive delay, with optional overlays."""
    now = 0.0
    monkeypatch.setattr("ax_devil.modules.synchronization.engine.time.monotonic", lambda: now)
    results: list[SyncResult[str, str]] = []
    sync: StreamSync[str, str] = StreamSync("delay-boundary", delay_ms=10)
    sync.set_output_callback(results.append)
    try:
        sync.push_frame("frame1", capture_time=1.0)
        sync.push_frame("frame2", capture_time=1.033)
        sync.push_frame("frame3", capture_time=1.066)
        if with_overlays:
            sync.push_overlay("overlay1", capture_time=1.028)
            sync.push_overlay("overlay2", capture_time=1.061)
        assert results == []

        now = 0.009
        sync.push_frame("frame4", capture_time=1.100)
        assert results == []
        now = 0.010
        sync.push_frame("frame5", capture_time=1.133)

        assert [result.frame.data for result in results] == ["frame1", "frame2", "frame3"]
        assert [result.overlay.data if result.overlay is not None else None for result in results] == (
            [None, "overlay1", "overlay2"] if with_overlays else [None, None, None]
        )
    finally:
        sync.cleanup()


def test_reset_discards_buffered_frames_and_overlays() -> None:
    results: list[SyncResult[str, str]] = []
    sync: StreamSync[str, str] = StreamSync(object_name="test", delay_ms=1_000)
    sync.set_output_callback(results.append)

    try:
        sync.push_frame("stale_frame", capture_time=1.0)
        sync.push_overlay("stale_overlay", capture_time=1.0)
        assert results == []

        sync.reset()
        sync.delay_s = 0

        sync.push_overlay("fresh_overlay", capture_time=1.995)
        sync.push_frame("fresh_frame", capture_time=2.0)

        assert len(results) == 1
        assert results[0].frame.data == "fresh_frame"
        assert results[0].overlay is not None
        assert results[0].overlay.data == "fresh_overlay"
    finally:
        sync.cleanup()


def test_overlay_arrival_releases_ready_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """Overlay input releases frames that became ready since the previous frame input."""
    now = 0.0
    monkeypatch.setattr("ax_devil.modules.synchronization.engine.time.monotonic", lambda: now)
    results: list[SyncResult[str, str]] = []
    sync: StreamSync[str, str] = StreamSync(object_name="test", delay_ms=10)
    sync.set_output_callback(results.append)

    try:
        sync.push_frame("frame", capture_time=2.0)
        now = 0.020
        assert results == []

        sync.push_overlay("overlay", capture_time=1.995)

        assert len(results) == 1
        assert results[0].frame.data == "frame"
        assert results[0].overlay is not None
        assert results[0].overlay.data == "overlay"
    finally:
        sync.cleanup()


def test_sync_diagnostics_clear_missing_offset_and_remove_closed_source() -> None:
    """Offsets have explicit units and never retain an earlier match for a missing overlay."""
    from ax_devil.modules.diagnostics.metrics_store import get_metrics_store, set_metrics_enabled

    set_metrics_enabled(True)
    store = get_metrics_store()
    sync: StreamSync[str, str] = StreamSync("diagnostic-sync", delay_ms=0)
    sync.set_output_callback(lambda _result: None)
    try:
        sync.push_overlay("overlay", 1.0)
        assert store.snapshot()["diagnostic-sync"].observations["Overlay queue"].value == 1
        sync.push_frame("frame", 1.005)
        assert store.snapshot()["diagnostic-sync"].observations["Overlay offset (ms)"].value == pytest.approx(5.0)
        sync.push_frame("frame without overlay", 2.0)
        assert store.snapshot()["diagnostic-sync"].observations["Overlay offset (ms)"].value is None
        sync.reset()
        assert store.snapshot()["diagnostic-sync"].observations["Frame queue"].value == 0
    finally:
        sync.cleanup()
    assert "diagnostic-sync" not in store.snapshot()


@pytest.mark.parametrize("arrival_times", [(1.028,), (1.061, 1.028, 1.025, 1.020)])
def test_live_policy_receives_latest_eligible_update_without_losing_future_samples(
    monkeypatch: pytest.MonkeyPatch, arrival_times: tuple[float, ...]
) -> None:
    """Buffered future and out-of-order samples reach the first eligible displayed frame."""
    now = 0.0
    monkeypatch.setattr("ax_devil.modules.synchronization.engine.time.monotonic", lambda: now)
    policy = OverlayPersistencePolicy(OverlayPersistenceSettings(enabled=True, timeout_ms=2050))
    selections: list[OverlaySelection] = []
    sync: StreamSync[FrameIdentifier, OverlayData] = StreamSync("policy-sync", delay_ms=10)

    def present(result: SyncResult[FrameIdentifier, OverlayData]) -> None:
        candidate = result.overlay.data if result.overlay is not None else None
        selections.append(policy.select_overlay(result.frame.data, candidate))

    sync.set_output_callback(present)
    try:
        for sequence, timestamp in enumerate((1.0, 1.033, 1.066, 1.100)):
            sync.push_frame(FrameIdentifier(sequence, timestamp * 1_000_000), timestamp)
        for sequence, timestamp in enumerate(arrival_times):
            overlay = OverlayData(
                content=Scene(time_slice=TimeSlice(sequence, sequence)),
                frame_id=FrameIdentifier(sequence, timestamp * 1_000_000),
                source_id="test",
            )
            sync.push_overlay(overlay, timestamp)
        assert selections == []
        now = 0.1
        sync.push_frame(FrameIdentifier(4, 1_133_000), 1.133)

        assert len(selections) == 4
        assert selections[0].overlay is None
        for selection, timestamp in zip(selections[1:], (1.033, 1.066, 1.100)):
            assert selection.overlay is not None
            expected = max(sample for sample in arrival_times if sample <= timestamp)
            assert selection.overlay.frame_id.timestamp_monotime_us == expected * 1_000_000
        assert selections[-1].reused
    finally:
        sync.cleanup()


@pytest.mark.parametrize("frame_time", [1.0, 1_789_776_000.0])
@pytest.mark.parametrize("age_us, matches", [(0, True), (10_000, True), (10_001, False)])
def test_live_match_age_boundary(frame_time: float, age_us: int, matches: bool) -> None:
    """Exact and up to 10 ms old overlays match, also with epoch-based camera timestamps."""
    results: list[SyncResult[str, str]] = []
    sync: StreamSync[str, str] = StreamSync("boundary", delay_ms=0)
    sync.set_output_callback(results.append)
    try:
        sync.push_overlay("overlay", frame_time - age_us / 1_000_000)
        sync.push_frame("frame", frame_time)
        assert (results[0].overlay is not None) is matches
    finally:
        sync.cleanup()


@pytest.mark.parametrize("sticky", [False, True])
def test_live_matching_precedes_sticky_retention(sticky: bool) -> None:
    """Only matched samples seed retention; stale arrivals cannot introduce or replace boxes."""
    policy = OverlayPersistencePolicy(OverlayPersistenceSettings(enabled=sticky, timeout_ms=2050))
    selections: list[OverlaySelection] = []
    sync: StreamSync[FrameIdentifier, OverlayData] = StreamSync("sticky-matching", delay_ms=0)

    def present(result: SyncResult[FrameIdentifier, OverlayData]) -> None:
        candidate = result.overlay.data if result.overlay is not None else None
        selections.append(policy.select_overlay(result.frame.data, candidate))

    def push_frame(timestamp_us: int) -> None:
        sync.push_frame(FrameIdentifier(0, timestamp_us), timestamp_us / 1_000_000)

    def push_overlay(timestamp_us: int) -> OverlayData:
        overlay = OverlayData(
            content=Scene(time_slice=TimeSlice(0, 0)),
            frame_id=FrameIdentifier(0, timestamp_us),
            source_id="test",
        )
        sync.push_overlay(overlay, timestamp_us / 1_000_000)
        return overlay

    sync.set_output_callback(present)
    try:
        push_overlay(900_000)
        push_frame(1_000_000)
        assert selections[-1].overlay is None

        matched = push_overlay(1_028_000)
        push_frame(1_033_000)
        assert selections[-1].overlay is matched
        assert not selections[-1].reused

        push_overlay(1_040_000)  # Newer than the cache, but missed its frame.
        push_frame(1_066_000)
        assert selections[-1].overlay is (matched if sticky else None)
        assert selections[-1].reused is sticky

        future = push_overlay(1_100_001)
        push_frame(1_100_000)  # Even 1 us in the future must wait.
        assert selections[-1].overlay is (matched if sticky else None)
        push_frame(1_110_001)
        assert selections[-1].overlay is future
        assert not selections[-1].reused

        push_frame(3_150_001)  # Sticky expiry includes its boundary.
        assert selections[-1].overlay is (future if sticky else None)
        push_frame(3_150_002)
        assert selections[-1].overlay is None
    finally:
        sync.cleanup()
