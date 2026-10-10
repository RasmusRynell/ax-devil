"""Tests for WorkspaceManager state, browser projection, and change notifications."""

from __future__ import annotations

import pytest

from ax_devil.modules.workspace.core import (
    ConsiderationItemRef,
    LiveMQTTOverlaySourceSpec,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    OverlayContent,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)
from ax_devil.modules.workspace.ui.workspace_manager import WorkspaceManager
from tests.helpers.contents import make_playlist, make_video


def test_remove_equal_content_instance_clears_consideration_state() -> None:
    state = WorkspaceManager()
    video = make_video("test.mp4", overlay_count=2)
    state.add_content(video)
    lane_ref = ConsiderationItemRef.video_lane(video.content_id, 1)
    state.set_item_considered(lane_ref, False)
    equal_copy = SeekableVideoContent(
        display_name=video.display_name,
        source_spec=video.source_spec,
        overlays=video.overlays,
        content_id=video.content_id,
    )

    state.remove_content(equal_copy)
    assert state.is_item_considered(lane_ref)


def test_consideration_defaults_and_updates_are_owned_by_manager() -> None:
    state = WorkspaceManager()
    video = make_video("a.mp4")
    lane = video.standalone_lanes()[0]
    playlist = PlaylistContent(
        display_name="Suite",
        entries=(
            PlaylistEntry(lanes=(lane,), default_considered=False),
            PlaylistEntry(
                lanes=(
                    lane,
                    lane.__class__(
                        display_name=lane.display_name,
                        video=lane.video,
                        default_considered=False,
                        overlay=lane.overlay,
                    ),
                ),
                default_considered=True,
            ),
        ),
    )

    state.add_content(playlist)

    entry_ref = ConsiderationItemRef.playlist_entry(playlist.content_id, 0)
    lane_ref = ConsiderationItemRef.playlist_lane(playlist.content_id, 1, 1)
    assert not state.is_item_considered(entry_ref)
    assert not state.is_item_considered(lane_ref)

    state.set_item_considered(entry_ref, True)
    assert state.is_item_considered(entry_ref)
    state.set_item_considered(entry_ref, True)


def test_invalid_lane_ref_does_not_affect_valid_lanes() -> None:
    state = WorkspaceManager()
    playlist = make_playlist()
    state.add_content(playlist)

    invalid_lane_ref = ConsiderationItemRef.playlist_lane(playlist.content_id, 0, 99)
    state.set_item_considered(invalid_lane_ref, False)

    valid_lane_ref = ConsiderationItemRef.playlist_lane(playlist.content_id, 0, 0)
    assert state.is_item_considered(valid_lane_ref)


def test_unknown_refs_are_ignored() -> None:
    state = WorkspaceManager()
    lane_ref = ConsiderationItemRef.video_lane("nonexistent", 0)

    state.set_item_considered(lane_ref, False)

    assert state.is_item_considered(lane_ref)


def test_live_content_logs_and_representations_exclude_credentials(caplog: pytest.LogCaptureFixture) -> None:
    """Removal warnings and nested source representations never expose credentials."""
    stream_url = "rtsp://synthetic-user:synthetic-device-secret@camera.local/stream?token=synthetic-token"
    content = LiveVideoContent(
        display_name=stream_url,
        source_spec=LiveRTSPStreamSpec(
            host="camera.local", username="synthetic-user", password="synthetic-device-secret", stream_url=stream_url
        ),
        overlays=(
            OverlayContent(
                "MQTT",
                LiveMQTTOverlaySourceSpec(
                    handler_type="LIVE",
                    broker_host="broker.local",
                    broker_username="synthetic-broker-user",
                    broker_password="synthetic-broker-secret",
                ),
            ),
        ),
    )
    manager = WorkspaceManager()
    with caplog.at_level("DEBUG"):
        manager.add_content(content)
        manager.remove_content(content)
        manager.remove_content(content)
    assert content.content_id in caplog.text
    assert "not in workspace" in caplog.text
    representations = f"{content.source_spec!r} {content.overlays!r}"
    for secret in (
        stream_url,
        "synthetic-user",
        "synthetic-device-secret",
        "synthetic-broker-user",
        "synthetic-broker-secret",
        "synthetic-token",
    ):
        assert secret not in caplog.text
        assert secret not in representations


def test_add_contents_emits_one_batch_signal() -> None:
    manager = WorkspaceManager()
    first = make_video("first.mp4")
    second = make_video("second.mp4")
    batches: list[list[object]] = []

    def record_added(contents: list[object]) -> None:
        assert manager.get_contents() == [first, second]
        batches.append(contents)

    manager.contents_added.connect(record_added)

    manager.add_contents([first, second])

    assert batches == [[first, second]]
    assert manager.get_contents() == [first, second]


def test_remove_content_emits_only_when_content_exists() -> None:
    manager = WorkspaceManager()
    video = make_video(overlay_count=1)
    removed: list[object] = []

    def record_removed(content: object) -> None:
        assert manager.get_contents() == []
        removed.append(content)

    manager.content_removed.connect(record_removed)
    manager.add_content(video)

    manager.remove_content(video)
    manager.remove_content(video)

    assert removed == [video]
    assert manager.get_contents() == []


def test_item_consideration_signal_emits_only_on_change() -> None:
    manager = WorkspaceManager()
    video = make_video(overlay_count=1)
    manager.add_content(video)
    changes: list[tuple[ConsiderationItemRef, bool]] = []
    manager.item_consideration_changed.connect(lambda ref, considered: changes.append((ref, considered)))
    lane_ref = ConsiderationItemRef.video_lane(video.content_id, 0)

    manager.set_item_considered(lane_ref, False)
    manager.set_item_considered(lane_ref, False)
    manager.set_item_considered(lane_ref, True)

    assert changes == [(lane_ref, False), (lane_ref, True)]
