"""Tests for WorkspaceManager Qt signal wrapping."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

from ax_devil.modules.workspace import (
    ConsiderationItemRef,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    OnScreenWorkspaceItem,
    OverlayContent,
    SeekableVideoContent,
    WorkspaceManager,
)


def _noop() -> NoReturn:
    raise NotImplementedError("stub")


def _make_video(name: str = "test.mp4") -> SeekableVideoContent:
    return SeekableVideoContent(
        display_name=name,
        source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")),
        overlays=(
            OverlayContent(
                display_name="overlay",
                source_spec=FileOverlaySourceSpec(path=Path(f"/tmp/{name}.json"), handler_type="TEST"),
            ),
        ),
    )


def test_add_contents_emits_one_batch_signal() -> None:
    manager = WorkspaceManager()
    first = _make_video("first.mp4")
    second = _make_video("second.mp4")
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
    video = _make_video()
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


def test_clear_emits_workspace_cleared_and_resets_state() -> None:
    manager = WorkspaceManager()
    video = _make_video()
    manager.add_content(video)
    lane_ref = ConsiderationItemRef.video_lane(video.content_id, 0)
    manager.set_item_considered(lane_ref, False)
    clears: list[bool] = []
    manager.workspace_cleared.connect(lambda: clears.append(True))

    manager.clear()

    assert clears == [True]
    assert manager.get_contents() == []
    assert manager.is_item_considered(lane_ref)


def test_item_consideration_signal_emits_only_on_change() -> None:
    manager = WorkspaceManager()
    video = _make_video()
    manager.add_content(video)
    changes: list[tuple[ConsiderationItemRef, bool]] = []
    manager.item_consideration_changed.connect(lambda ref, considered: changes.append((ref, considered)))
    lane_ref = ConsiderationItemRef.video_lane(video.content_id, 0)

    manager.set_item_considered(lane_ref, False)
    manager.set_item_considered(lane_ref, False)
    manager.set_item_considered(lane_ref, True)

    assert changes == [(lane_ref, False), (lane_ref, True)]


def test_browser_rows_accept_current_open_items() -> None:
    manager = WorkspaceManager()
    video = _make_video()
    manager.add_content(video)
    open_items = frozenset({OnScreenWorkspaceItem(kind="video", content_id=video.content_id)})

    assert manager.get_browser_rows(open_items)[0].is_open
    assert not manager.get_browser_rows()[0].is_open
