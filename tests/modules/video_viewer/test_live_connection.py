"""Tests for the live connection status model."""

from __future__ import annotations

from ax_devil.modules.video_viewer.live_connection import (
    LiveConnectionState,
    LiveConnectionStatus,
    LiveFeed,
    LiveFeedStatus,
)


def test_feed_status_transitions_keep_reason_and_count_retries() -> None:
    connecting = LiveFeedStatus(LiveFeed.OVERLAY)
    first = connecting.reconnecting("Connection refused")
    second = first.reconnecting("Connection refused")

    assert second.state is LiveConnectionState.RECONNECTING
    assert "(2)" in second.headline
    assert "Overlay" in second.summary and "Connection refused" in second.summary
    assert second.live() == LiveFeedStatus(LiveFeed.OVERLAY, LiveConnectionState.LIVE)
    failed = second.failed("Unsupported data source")
    assert failed.state is LiveConnectionState.FAILED
    assert failed.reason == "Unsupported data source"


def test_only_a_live_feed_can_stall() -> None:
    video = LiveFeedStatus(LiveFeed.VIDEO)

    assert video.stalled(6.0) is video
    stalled = video.live().stalled(6.2)
    assert stalled.state is LiveConnectionState.STALLED
    assert "6 s" in stalled.reason
    failed = video.failed("Unauthorized")
    assert failed.stalled(9.0) is failed


def test_connection_status_reports_problems_retry_and_placeholder_from_feeds() -> None:
    status = LiveConnectionStatus.connecting([LiveFeed.VIDEO, LiveFeed.OVERLAY])

    assert status.problems == ()
    assert not status.needs_retry

    status = status.with_status(status.feed(LiveFeed.VIDEO).live())
    status = status.with_status(status.feed(LiveFeed.OVERLAY).reconnecting("Connection refused"))

    assert status.headline.state is LiveConnectionState.LIVE
    assert [problem.feed for problem in status.problems] == [LiveFeed.OVERLAY]
    assert not status.needs_retry

    status = status.with_status(status.feed(LiveFeed.VIDEO).failed("RTSP Error: Unauthorized"))

    assert [problem.feed for problem in status.problems] == [LiveFeed.VIDEO, LiveFeed.OVERLAY]
    assert status.needs_retry
    assert "RTSP Error: Unauthorized" in status.placeholder_text
