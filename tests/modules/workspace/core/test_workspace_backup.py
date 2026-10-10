"""The workspace kept between launches, and the recent workspace list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ax_devil.modules.workspace.core import (
    LiveStreamItem,
    OverlayFile,
    RecentWorkspaces,
    UnreadableItem,
    VideoItem,
    Workspace,
    WorkspaceBackup,
    item_from_json,
    load_workspace,
    save_workspace,
)


def _modified(current: Workspace, saved: Workspace) -> bool:
    return current != saved


def test_an_untitled_workspace_is_kept_with_absolute_paths_and_restored_as_modified(tmp_path: Path) -> None:
    video = VideoItem(
        label="Lot", video=tmp_path / "clips" / "lot.mp4", overlays=(OverlayFile(tmp_path / "a.json", "X"),)
    )
    live = LiveStreamItem(host="$AX_DEVIL_TARGET_ADDR", password="$AX_DEVIL_TARGET_PASS")
    unreadable = item_from_json({"kind": "radar", "id": "r1", "range": 9}, None)
    assert isinstance(unreadable, UnreadableItem)
    backup = WorkspaceBackup(tmp_path / "state" / "workspace-backup.json")

    backup.keep(Workspace(items=(video, live, unreadable)))
    document = json.loads((tmp_path / "state" / "workspace-backup.json").read_text())
    current, saved = WorkspaceBackup(tmp_path / "state" / "workspace-backup.json").restore()

    assert document["path"] is None
    assert document["items"][0]["video"] == str(tmp_path / "clips" / "lot.mp4")
    assert document["items"][1]["host"] == "$AX_DEVIL_TARGET_ADDR"
    assert document["items"][2] == {"kind": "radar", "id": "r1", "range": 9}
    assert current == Workspace(items=(video, live, unreadable))
    assert saved == Workspace()
    assert _modified(current, saved)


def test_a_saved_workspace_is_restored_with_its_file_as_the_saved_state(tmp_path: Path) -> None:
    folder = tmp_path / "reviews"
    clip = VideoItem(video=folder / "clips" / "a.mp4")
    saved_file = save_workspace(Workspace(items=(clip,)), folder / "Lot.ax-devil.workspace")
    backup = WorkspaceBackup(tmp_path / "workspace-backup.json")

    backup.keep(saved_file)
    assert not _modified(*backup.restore())

    edited = saved_file.add_items([VideoItem(video=folder / "clips" / "b.mp4")])
    backup.keep(edited)
    current, saved = backup.restore()
    assert (current, saved) == (edited, load_workspace(saved_file.path or Path()))
    assert current.name == "Lot" and _modified(current, saved)


def test_items_of_a_saved_workspace_whose_file_is_gone_are_kept_as_untitled(tmp_path: Path) -> None:
    saved_file = save_workspace(
        Workspace(items=(VideoItem(video=tmp_path / "a.mp4"),)), tmp_path / "Lot.ax-devil.workspace"
    )
    backup = WorkspaceBackup(tmp_path / "workspace-backup.json")
    backup.keep(saved_file)
    (tmp_path / "Lot.ax-devil.workspace").unlink()

    current, saved = backup.restore()

    assert current == Workspace(items=saved_file.items)
    assert saved == Workspace()
    assert current.name == "Untitled" and _modified(current, saved)


@pytest.mark.parametrize("content", [None, "{not json", "[]", '{"items": 3}', '{"items": [{"id": "a"}, {"id": "a"}]}'])
def test_a_missing_or_unusable_backup_restores_the_empty_untitled_workspace(
    tmp_path: Path, content: str | None
) -> None:
    path = tmp_path / "workspace-backup.json"
    if content is not None:
        path.write_text(content)

    assert WorkspaceBackup(path).restore() == (Workspace(), Workspace())


def test_recent_workspaces_are_newest_first_limited_and_drop_files_that_are_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = [tmp_path / f"w{index}.ax-devil.workspace" for index in range(4)]
    for file in files:
        file.write_text("{}")
    store = tmp_path / "state" / "recent-workspaces.json"
    recent = RecentWorkspaces(store, limit=3)
    for file in files:
        recent.record(file)
    monkeypatch.chdir(tmp_path)
    recent.record(Path(files[1].name))

    assert RecentWorkspaces(store, limit=3).entries() == (files[1], files[3], files[2])
    files[3].unlink()
    assert recent.entries() == (files[1], files[2])
    recent.record(files[0])
    assert recent.entries() == (files[0], files[1], files[2])


def test_recent_workspaces_keep_the_path_the_user_chose_not_its_symlink_target(tmp_path: Path) -> None:
    target = tmp_path / "real" / "w.ax-devil.workspace"
    target.parent.mkdir()
    target.write_text("{}")
    link = tmp_path / "link.ax-devil.workspace"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not supported here")
    recent = RecentWorkspaces(tmp_path / "recent-workspaces.json")

    recent.record(link)

    assert recent.entries() == (link,)


@pytest.mark.parametrize("content", ["{not json", "{}", "[3, null]"])
def test_an_unreadable_recent_workspaces_file_is_ignored(tmp_path: Path, content: str) -> None:
    path = tmp_path / "recent-workspaces.json"
    path.write_text(content)

    assert RecentWorkspaces(path).entries() == ()


def test_unreadable_items_without_an_id_do_not_make_a_reopened_workspace_modified(tmp_path: Path) -> None:
    path = tmp_path / "Lot.ax-devil.workspace"
    path.write_text(json.dumps({"version": 1, "items": [{"kind": "radar"}, {"kind": "radar"}, "junk"]}))
    backup = WorkspaceBackup(tmp_path / "workspace-backup.json")

    opened = load_workspace(path)
    assert load_workspace(path) == opened
    backup.keep(opened)

    assert not _modified(*backup.restore())
