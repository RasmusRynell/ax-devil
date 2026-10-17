"""The workspace lifecycle: keeping unsaved edits, New/Open/Save, and launches."""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.core import (
    RecentWorkspaces,
    VideoItem,
    Workspace,
    load_workspace,
    save_workspace,
)
from ax_devil.modules.workspace.ui import workspace_lifecycle
from ax_devil.modules.workspace.ui.workspace_lifecycle import WorkspaceLifecycle
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.workspace import FakePrompts, inline_resolver, quit_app


@dataclass(frozen=True)
class _Launch:
    """One run of the app: the store it holds, the prompts it answers with, and the lifecycle driving the store."""

    store: WorkspaceStore
    prompts: FakePrompts
    lifecycle: WorkspaceLifecycle


class _Launches:
    """Build launches that share one storage folder, as consecutive launches of the app do."""

    def __init__(self, tmp_path: Path) -> None:
        self._state = tmp_path / "state"

    def new(self, prompts: FakePrompts | None = None) -> _Launch:
        prompts = prompts or FakePrompts()
        store = WorkspaceStore(inline_resolver())
        lifecycle = WorkspaceLifecycle(
            store,
            prompts,
            lambda: self._state / "unsaved-workspaces",
            RecentWorkspaces(lambda: self._state / "recent-workspaces.json"),
        )
        return _Launch(store, prompts, lifecycle)


@pytest.fixture()
def launches(tmp_path: Path) -> _Launches:
    return _Launches(tmp_path)


def _video(tmp_path: Path, name: str) -> VideoItem:
    path = tmp_path / name
    path.write_bytes(b"")
    return VideoItem(video=path)


def _record_added_flags(store: WorkspaceStore) -> list[bool]:
    """Return the list that collects the *added* flag of every resolution the store commits from now on."""
    flags: list[bool] = []
    store.items_resolved.connect(lambda _resolutions, added: flags.append(added))
    return flags


def test_a_launch_restores_the_unsaved_edits_a_quit_left_and_saving_them_replaces_the_copy(
    launches: _Launches, tmp_path: Path
) -> None:
    original = tmp_path / "Lot.ax-devil.workspace"
    saving = launches.new(FakePrompts(save_path=original))
    saved_clip, edit = _video(tmp_path, "a.mp4"), _video(tmp_path, "b.mp4")
    saving.store.add_items([saved_clip])
    saving.lifecycle.save_workspace()
    saving.store.add_items([edit])
    quit_app(saving.lifecycle)
    assert load_workspace(original).items == (saved_clip,)

    second = launches.new()
    recovered: list[None] = []
    second.lifecycle.recovered.connect(lambda: recovered.append(None))
    second.lifecycle.launch()

    assert recovered == [None]
    assert (second.store.workspace.path, second.store.workspace.items) == (original, (saved_clip, edit))
    assert second.store.is_modified
    assert second.lifecycle.save_workspace()
    assert load_workspace(original).items == (saved_clip, edit)
    assert not second.store.is_modified
    assert list((tmp_path / "state" / "unsaved-workspaces").iterdir()) == []
    assert [entry.path for entry in second.lifecycle.recent_workspaces()] == [original]


def test_unsaved_edits_are_copied_while_the_app_runs_and_only_the_instance_that_got_there_first_restores_them(
    qtbot: QtBot, launches: _Launches, tmp_path: Path
) -> None:
    clip = _video(tmp_path, "lot.mp4")
    first = launches.new()
    first.store.add_items([clip])
    qtbot.waitUntil(lambda: first.lifecycle.recent_workspaces() != (), timeout=5000)

    while_running = launches.new()
    while_running.lifecycle.launch()

    assert while_running.store.workspace == Workspace()
    quit_app(first.lifecycle)

    after = launches.new()
    after.lifecycle.launch()

    assert after.store.workspace.items == (clip,)
    assert after.store.is_modified


def test_quitting_instances_with_the_same_workspace_name_at_the_same_moment_keep_both_copies(
    launches: _Launches, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz: object = None) -> _Frozen:
            return cls(2026, 1, 2, 3, 4, 5)

    monkeypatch.setattr(workspace_lifecycle, "datetime", _Frozen)
    clips = [_video(tmp_path, "a.mp4"), _video(tmp_path, "b.mp4")]
    for clip in clips:
        instance = launches.new()
        instance.store.add_items([clip])
        instance.store.rename_workspace("Parking lot")
        instance.lifecycle.keep_unsaved_changes()

    copies = launches.new().lifecycle.recent_workspaces()
    assert sorted(load_workspace(copy.path).items[0].id for copy in copies) == sorted(clip.id for clip in clips)


@pytest.mark.parametrize("name", ["x" * 400, "\u00e5" * 400], ids=["ascii", "multibyte"])
def test_a_quit_keeps_unsaved_edits_of_a_workspace_with_a_very_long_name(
    launches: _Launches, tmp_path: Path, name: str
) -> None:
    launch = launches.new()
    launch.store.add_items([_video(tmp_path, "a.mp4")])
    launch.store.rename_workspace(name)

    launch.lifecycle.keep_unsaved_changes()

    [copy] = launch.lifecycle.recent_workspaces()
    assert load_workspace(copy.path).name == name


def test_a_quit_keeps_unsaved_edits_of_a_workspace_whose_name_has_characters_a_file_name_cannot_hold(
    launches: _Launches, tmp_path: Path
) -> None:
    name = "Lot\x00 1\n2"
    launch = launches.new()
    launch.store.add_items([_video(tmp_path, "a.mp4")])
    launch.store.rename_workspace(name)

    launch.lifecycle.keep_unsaved_changes()

    [copy] = launch.lifecycle.recent_workspaces()
    assert load_workspace(copy.path).name == name


def test_a_quit_keeps_unsaved_edits_of_a_workspace_whose_name_has_a_lone_surrogate(
    launches: _Launches, tmp_path: Path
) -> None:
    name = "Lot \ud800 1"
    launch = launches.new()
    launch.store.add_items([_video(tmp_path, "a.mp4")])
    launch.store.rename_workspace(name)

    launch.lifecycle.keep_unsaved_changes()

    [copy] = launch.lifecycle.recent_workspaces()
    assert load_workspace(copy.path).name == name


def test_a_quit_keeps_unsaved_edits_when_the_file_system_refuses_the_file_name(
    launches: _Launches, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = os.open

    def refuse_names_with_the_workspace_name(path: Any, *args: Any, **kwargs: Any) -> int:
        if "Parking lot" in str(path):
            raise OSError(errno.ENAMETOOLONG, "File name too long")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", refuse_names_with_the_workspace_name)
    clip = _video(tmp_path, "a.mp4")
    launch = launches.new()
    launch.store.add_items([clip])
    launch.store.rename_workspace("Parking lot")

    launch.lifecycle.keep_unsaved_changes()

    [copy] = launch.lifecycle.recent_workspaces()
    assert copy.path.name.startswith("Unsaved workspace ")
    saved = load_workspace(copy.path)
    assert (saved.name, saved.items) == ("Parking lot", (clip,))


def test_a_quit_without_unsaved_edits_keeps_nothing(launches: _Launches, tmp_path: Path) -> None:
    empty = launches.new()
    empty.lifecycle.keep_unsaved_changes()
    saved = launches.new(FakePrompts(save_path=tmp_path / "Lot.ax-devil.workspace"))
    saved.store.add_items([_video(tmp_path, "a.mp4")])
    saved.lifecycle.save_workspace()

    saved.lifecycle.keep_unsaved_changes()

    assert [entry.path for entry in saved.lifecycle.recent_workspaces()] == [tmp_path / "Lot.ax-devil.workspace"]
    assert not (tmp_path / "state" / "unsaved-workspaces").exists()


def test_a_quit_that_cannot_write_its_copy_does_not_raise(launches: _Launches, tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "unsaved-workspaces").write_text("a file where the folder should be")
    launch = launches.new()
    launch.store.add_items([_video(tmp_path, "a.mp4")])

    launch.lifecycle.keep_unsaved_changes()

    assert launch.lifecycle.recent_workspaces() == ()


def test_saving_an_untitled_workspace_asks_where_appends_the_extension_and_remembers_it(
    launches: _Launches, tmp_path: Path
) -> None:
    launch = launches.new(FakePrompts(save_path=tmp_path / "reviews" / "Lot"))
    launch.store.add_items([_video(tmp_path, "lot.mp4")])
    launch.store.rename_workspace("Parking lot")

    assert launch.lifecycle.save_workspace()

    saved = tmp_path / "reviews" / "Lot.ax-devil.workspace"
    assert load_workspace(saved).items == launch.store.workspace.items
    assert (launch.store.workspace.name, launch.store.is_modified) == ("Parking lot", False)
    assert load_workspace(saved).name == "Parking lot"
    assert [entry.path for entry in launch.lifecycle.recent_workspaces()] == [saved]


def _modified_launch(launches: _Launches, tmp_path: Path, prompts: FakePrompts) -> tuple[_Launch, VideoItem]:
    launch = launches.new(prompts)
    clip = _video(tmp_path, "kept.mp4")
    launch.store.add_items([clip])
    return launch, clip


def test_opening_a_workspace_keeps_the_unsaved_edits_as_a_copy_in_recents_without_asking(
    launches: _Launches, tmp_path: Path
) -> None:
    other = save_workspace(
        Workspace(name="Other", items=(_video(tmp_path, "other.mp4"),)), tmp_path / "Other.ax-devil.workspace"
    )
    prompts = FakePrompts(open_path=other.path)
    launch, clip = _modified_launch(launches, tmp_path, prompts)
    added = _record_added_flags(launch.store)

    assert launch.lifecycle.open_workspace()

    assert launch.store.workspace.items == other.items
    assert (launch.store.workspace.name, launch.store.is_modified) == ("Other", False)
    assert added == [False]
    [opened, copy] = launch.lifecycle.recent_workspaces()
    assert opened.path == other.path
    assert load_workspace(copy.path).items == (clip,)

    assert launch.lifecycle.open_workspace(copy.path)
    assert (launch.store.workspace.path, launch.store.workspace.items) == (None, (clip,))
    assert launch.store.is_modified
    assert prompts.errors == []


def test_cancelling_the_open_dialog_asks_nothing(launches: _Launches, tmp_path: Path) -> None:
    prompts = FakePrompts(open_path=None)
    launch, clip = _modified_launch(launches, tmp_path, prompts)

    assert not launch.lifecycle.open_workspace()

    assert launch.store.workspace.items == (clip,)


def test_a_workspace_file_that_cannot_be_read_is_reported_and_changes_nothing(
    launches: _Launches, tmp_path: Path
) -> None:
    damaged = tmp_path / "Damaged.ax-devil.workspace"
    damaged.write_text("{not json")
    prompts = FakePrompts()
    launch, clip = _modified_launch(launches, tmp_path, prompts)

    assert not launch.lifecycle.open_workspace(damaged)

    [error] = prompts.errors
    assert error.startswith(f"{damaged.name} is not valid JSON")
    assert launch.store.workspace.items == (clip,) and launch.store.is_modified


def test_a_launch_with_content_starts_an_untitled_workspace_with_it_without_asking(
    launches: _Launches, tmp_path: Path
) -> None:
    prompts = FakePrompts()
    launched = launches.new(prompts)
    added = _record_added_flags(launched.store)
    from_cli = _video(tmp_path, "cli.mp4")

    launched.lifecycle.launch([from_cli])

    assert launched.store.workspace.items == (from_cli,)
    assert added == [True]


def test_a_failed_launch_open_leaves_the_empty_workspace_and_reports_the_file(
    launches: _Launches, tmp_path: Path
) -> None:
    damaged = tmp_path / "Damaged.ax-devil.workspace"
    damaged.write_text("{not json")
    launched = launches.new()

    launched.lifecycle.launch(workspace_file=damaged)

    assert launched.store.workspace == Workspace()
    assert len(launched.prompts.errors) == 1


def test_a_recent_workspace_that_cannot_be_opened_is_forgotten(launches: _Launches, tmp_path: Path) -> None:
    saved = tmp_path / "Lot.ax-devil.workspace"
    save_workspace(Workspace(), saved)
    launch = launches.new(FakePrompts())
    assert launch.lifecycle.open_workspace(saved)
    assert [entry.path for entry in launch.lifecycle.recent_workspaces()] == [saved]
    saved.unlink()

    assert not launch.lifecycle.open_workspace(saved)

    assert launch.lifecycle.recent_workspaces() == ()


def test_a_launch_with_a_workspace_file_opens_it(launches: _Launches, tmp_path: Path) -> None:
    saved = save_workspace(
        Workspace(name="Lot", items=(_video(tmp_path, "a.mp4"),)), tmp_path / "Lot.ax-devil.workspace"
    )
    launch = launches.new()
    added = _record_added_flags(launch.store)

    launch.lifecycle.launch(workspace_file=saved.path)

    assert launch.store.workspace.items == saved.items
    assert (launch.store.workspace.name, launch.store.is_modified) == ("Lot", False)
    assert added == [False]
    assert [entry.path for entry in launch.lifecycle.recent_workspaces()] == [saved.path]


@pytest.mark.parametrize("replace", [False, True])
def test_save_as_asks_before_replacing_the_file_the_appended_extension_names(
    launches: _Launches, tmp_path: Path, replace: bool
) -> None:
    existing = save_workspace(Workspace(), tmp_path / "Lot.ax-devil.workspace")
    prompts = FakePrompts(save_path=tmp_path / "Lot", replace=replace)
    launch = launches.new(prompts)
    clip = _video(tmp_path, "lot.mp4")
    launch.store.add_items([clip])

    assert launch.lifecycle.save_workspace_as() is replace

    assert prompts.asked_to_replace == [existing.path]
    assert load_workspace(tmp_path / "Lot.ax-devil.workspace").items == ((clip,) if replace else ())
    assert launch.store.is_modified is not replace


def test_reopening_the_saved_workspace_replaces_it_and_resolves_its_items_again(
    launches: _Launches, tmp_path: Path
) -> None:
    """Reopening the file the current Workspace came from replaces it, so no item keeps Content from before."""
    launch = launches.new(FakePrompts(save_path=tmp_path / "Lot.ax-devil.workspace"))
    clip = _video(tmp_path, "lot.mp4")
    launch.store.add_items([clip])
    launch.lifecycle.save_workspace()
    replacements: list[bool] = []
    launch.store.workspace_replaced.connect(lambda: replacements.append(True))
    added = _record_added_flags(launch.store)

    assert launch.lifecycle.open_workspace(tmp_path / "Lot.ax-devil.workspace")

    assert replacements == [True]
    assert added == [False]
    assert launch.store.workspace.items == (clip,)
    assert [resolution.item for resolution in launch.store.resolutions()] == [clip]
    assert not any(resolution.is_pending for resolution in launch.store.resolutions())


def _break_recent_workspaces(tmp_path: Path) -> Path:
    """Make the recent workspaces list unwritable, as an inaccessible lock file does; return the lock path."""
    lock = tmp_path / "state" / "recent-workspaces.json.lock"
    lock.mkdir(parents=True)
    return lock


def test_a_quit_whose_copy_cannot_be_listed_leaves_the_copy_on_disk(launches: _Launches, tmp_path: Path) -> None:
    _break_recent_workspaces(tmp_path)
    clip = _video(tmp_path, "a.mp4")
    launch = launches.new()
    launch.store.add_items([clip])

    launch.lifecycle.keep_unsaved_changes()

    assert launch.lifecycle.recent_workspaces() == ()
    [copy] = (tmp_path / "state" / "unsaved-workspaces").glob("*.ax-devil.workspace")
    assert load_workspace(copy).items == (clip,)
