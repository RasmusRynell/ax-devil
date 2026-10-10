"""Workspace files: what is written, what is read back, and what happens to items and files that cannot be read."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_devil.modules.workspace.core import (
    ItemResolutionError,
    LiveOverlayMode,
    LiveStreamItem,
    OverlayFile,
    PlaylistItem,
    UnreadableItem,
    VideoItem,
    Workspace,
    WorkspaceFileError,
    load_workspace,
    save_workspace,
)
from tests.helpers.workspace import FakeResolutionContext


def _write(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _written(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def test_every_kind_round_trips_with_ids_labels_order_and_fields(tmp_path: Path) -> None:
    items = (
        VideoItem(label="Lot", video=tmp_path / "a.mp4", overlays=(OverlayFile(tmp_path / "a.json", "adf-v1"),)),
        LiveStreamItem(
            label="Entrance",
            host="$AX_DEVIL_TARGET_ADDR",
            username="root",
            password="$AX_DEVIL_TARGET_PASS",
            camera_head=2,
            resolution="640x480",
            stream_url="rtsp://camera/stream",
            overlay_mode=LiveOverlayMode.NONE,
            handler_type="MQTT",
            mqtt_host="broker",
            mqtt_port=8883,
            mqtt_username="user",
            mqtt_password="secret",
            analytics_data_source_key="com.axis.scene",
            device_api_protocol="http",
            websocket_topic="topic",
            websocket_channel_id=3,
        ),
        PlaylistItem(label="Exp 3", resolver="mot_challenge", settings={"root": "runs/exp3", "sequences": ["a", "b"]}),
    )
    path = tmp_path / "work.ax-devil.workspace"

    saved = save_workspace(Workspace(items=items), path)
    loaded = load_workspace(path)

    assert loaded == saved
    assert loaded.items == items
    assert loaded.path == path
    assert loaded.name == "work"


@pytest.mark.parametrize("mode", list(LiveOverlayMode))
def test_live_overlay_mode_is_written_as_its_value(tmp_path: Path, mode: LiveOverlayMode) -> None:
    path = tmp_path / "w.ax-devil.workspace"
    item = LiveStreamItem(label="Cam", host="camera", overlay_mode=mode)

    save_workspace(Workspace(items=(item,)), path)

    assert _written(path)["items"][0]["overlay_mode"] == mode.value
    assert load_workspace(path).items == (item,)


def test_paths_inside_the_workspace_folder_are_relative_and_others_absolute(tmp_path: Path) -> None:
    folder, elsewhere = tmp_path / "ws", tmp_path / "elsewhere"
    path = folder / "w.ax-devil.workspace"
    item = VideoItem(
        label="Clip",
        video=folder / "clips" / "lot.mp4",
        overlays=(OverlayFile(elsewhere / "lot.json", "adf-v1"),),
    )

    save_workspace(Workspace(items=(item,)), path)

    written = _written(path)["items"][0]
    assert written["video"] == "clips/lot.mp4"
    assert written["overlays"] == [{"path": str(elsewhere / "lot.json"), "decoder": "adf-v1"}]
    assert load_workspace(path).items == (item,)


def test_dot_dot_segments_are_normalized_before_checking_the_folder(tmp_path: Path) -> None:
    folder = tmp_path / "ws"
    path = folder / "w.ax-devil.workspace"
    inside = VideoItem(label="Inside", video=folder / "clips" / ".." / "lot.mp4")
    outside = VideoItem(label="Outside", video=folder / ".." / "lot.mp4")

    save_workspace(Workspace(items=(inside, outside)), path)

    inside_json, outside_json = _written(path)["items"]
    assert inside_json["video"] == "lot.mp4"
    assert outside_json["video"] == str(tmp_path / "lot.mp4")


def test_relative_paths_are_read_against_the_files_folder_wherever_it_moved(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "w.ax-devil.workspace",
        {"version": 1, "items": [{"kind": "video", "id": "v", "label": "Clip", "video": "clips/lot.mp4"}]},
    )

    (item,) = load_workspace(path).items

    assert isinstance(item, VideoItem) and item.video == tmp_path / "clips" / "lot.mp4"


def test_credentials_are_written_as_stored(tmp_path: Path) -> None:
    path = tmp_path / "w.ax-devil.workspace"
    item = LiveStreamItem(label="Cam", host="$ADDR", username="$USER", password="hunter2")

    save_workspace(Workspace(items=(item,)), path)

    written = _written(path)["items"][0]
    assert (written["host"], written["username"], written["password"]) == ("$ADDR", "$USER", "hunter2")


def test_the_file_is_indented_json_with_a_version_and_a_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "w.ax-devil.workspace"

    save_workspace(Workspace(items=(PlaylistItem(label="P", resolver="r", settings={"root": "x"}),)), path)

    text = path.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert '\n  "version": 1' in text
    assert list(tmp_path.iterdir()) == [path]


def test_a_failed_save_keeps_the_old_file_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "w.ax-devil.workspace"
    save_workspace(Workspace(items=(PlaylistItem(label="Old", resolver="r"),)), path)
    before = path.read_text(encoding="utf-8")
    unwritable = PlaylistItem(label="New", resolver="r", settings={"bad": object()})

    with pytest.raises(WorkspaceFileError):
        save_workspace(Workspace(items=(unwritable,)), path)

    assert path.read_text(encoding="utf-8") == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(sys.platform == "win32", reason="permission bits are POSIX")
def test_saving_over_an_existing_file_keeps_its_permissions(tmp_path: Path) -> None:
    path = tmp_path / "w.ax-devil.workspace"
    save_workspace(Workspace(items=(PlaylistItem(label="Old", resolver="r"),)), path)
    path.chmod(0o664)

    save_workspace(Workspace(items=(PlaylistItem(label="New", resolver="r"),)), path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o664
    assert load_workspace(path).items[0].label == "New"


@pytest.mark.skipif(sys.platform == "win32", reason="permission bits are POSIX")
def test_a_new_workspace_file_is_private_to_its_owner(tmp_path: Path) -> None:
    path = tmp_path / "w.ax-devil.workspace"

    save_workspace(Workspace(items=(PlaylistItem(label="New", resolver="r"),)), path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "raw",
    [
        {"kind": "hologram", "id": "x1", "label": "Future", "beam": 3},
        {"kind": "video", "id": "x1", "label": "Broken", "video": 12},
        {"kind": "live_stream", "id": "x1", "label": "Broken", "host": "h", "overlay_mode": "nonsense"},
        {"kind": "playlist", "id": "x1", "label": "Broken", "resolver": "r", "settings": ["not", "an", "object"]},
        {"kind": "video", "id": "x1", "label": "Broken", "video": "a.mp4", "overlays": [7]},
    ],
)
def test_items_that_cannot_be_read_stay_unresolvable_and_are_saved_back_unchanged(
    tmp_path: Path, raw: dict[str, Any]
) -> None:
    good = {"kind": "playlist", "id": "ok", "label": "Fine", "resolver": "r", "settings": {}}
    path = _write(tmp_path / "w.ax-devil.workspace", {"version": 1, "items": [raw, good]})

    loaded = load_workspace(path)

    bad, fine = loaded.items
    assert isinstance(bad, UnreadableItem) and isinstance(fine, PlaylistItem)
    assert (bad.id, bad.label) == ("x1", raw["label"])
    with pytest.raises(ItemResolutionError):
        bad.resolve(FakeResolutionContext())
    resaved = tmp_path / "again.ax-devil.workspace"
    save_workspace(loaded, resaved)
    assert _written(resaved)["items"] == [raw, good]


def test_an_unreadable_item_without_id_or_label_gets_an_id_and_its_kind_as_label(tmp_path: Path) -> None:
    path = _write(tmp_path / "w.ax-devil.workspace", {"version": 1, "items": [{"kind": "hologram"}, "junk"]})

    first, second = load_workspace(path).items

    assert first.label == "hologram" and first.id
    assert isinstance(second, UnreadableItem) and second.id != first.id


def test_an_unknown_kind_explains_itself_when_resolved(tmp_path: Path) -> None:
    path = _write(tmp_path / "w.ax-devil.workspace", {"version": 1, "items": [{"kind": "hologram", "id": "a"}]})

    (item,) = load_workspace(path).items

    with pytest.raises(ItemResolutionError, match="hologram"):
        item.resolve(FakeResolutionContext())


@pytest.mark.parametrize(
    "document",
    [
        {"items": []},
        {"version": 2, "items": []},
        {"version": 1},
        {"version": 1, "items": {"a": 1}},
        [],
        {"version": 1, "items": [{"kind": "playlist", "id": "a", "label": "A", "resolver": "r"}] * 2},
    ],
)
def test_a_damaged_file_raises_one_error(tmp_path: Path, document: object) -> None:
    with pytest.raises(WorkspaceFileError):
        load_workspace(_write(tmp_path / "w.ax-devil.workspace", document))


def test_invalid_json_and_missing_files_raise_the_same_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.ax-devil.workspace"
    broken.write_text("{not json", encoding="utf-8")

    with pytest.raises(WorkspaceFileError, match="not valid JSON"):
        load_workspace(broken)
    with pytest.raises(WorkspaceFileError):
        load_workspace(tmp_path / "missing.ax-devil.workspace")
