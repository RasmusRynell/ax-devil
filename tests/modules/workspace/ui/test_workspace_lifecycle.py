"""The workspace lifecycle: keeping and restoring, New/Open/Save with the modified prompt, launches, and rename."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from ax_devil.modules.workspace.core import (
    RecentWorkspaces,
    VideoItem,
    Workspace,
    WorkspaceBackup,
    load_workspace,
    save_workspace,
)
from ax_devil.modules.workspace.ui.workspace_lifecycle import WorkspaceLifecycle
from ax_devil.modules.workspace.ui.workspace_prompts import SaveChoice
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.workspace import FakePrompts, inline_resolver


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
            WorkspaceBackup(lambda: self._state / "workspace-backup.json"),
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


def test_closing_keeps_an_untitled_workspace_and_launch_lists_it_without_opening_anything(
    launches: _Launches, tmp_path: Path
) -> None:
    first = launches.new()
    clip = _video(tmp_path, "lot.mp4")
    first.store.add_items([clip])
    first.lifecycle.keep_workspace()

    second = launches.new()
    added = _record_added_flags(second.store)
    second.lifecycle.launch()

    assert second.store.workspace.items == (clip,)
    assert added == [False]
    assert (second.store.workspace.name, second.store.is_modified) == ("Untitled", True)


def test_saving_an_untitled_workspace_asks_where_appends_the_extension_and_remembers_it(
    launches: _Launches, tmp_path: Path
) -> None:
    launch = launches.new(FakePrompts(save_path=tmp_path / "reviews" / "Lot"))
    launch.store.add_items([_video(tmp_path, "lot.mp4")])

    assert launch.lifecycle.save_workspace()

    saved = tmp_path / "reviews" / "Lot.ax-devil.workspace"
    assert load_workspace(saved).items == launch.store.workspace.items
    assert (launch.store.workspace.name, launch.store.is_modified) == ("Lot", False)
    assert launch.lifecycle.recent_workspaces() == (saved,)


def test_a_restored_saved_workspace_is_modified_only_when_it_had_unsaved_edits(
    launches: _Launches, tmp_path: Path
) -> None:
    saving = launches.new(FakePrompts(save_path=tmp_path / "Lot.ax-devil.workspace"))
    saving.store.add_items([_video(tmp_path, "a.mp4")])
    saving.lifecycle.save_workspace()
    saving.lifecycle.keep_workspace()

    unchanged = launches.new()
    unchanged.lifecycle.launch()
    assert (unchanged.store.workspace.name, unchanged.store.is_modified) == ("Lot", False)

    unchanged.store.add_items([_video(tmp_path, "b.mp4")])
    unchanged.lifecycle.keep_workspace()
    edited = launches.new()
    edited.lifecycle.launch()
    assert (edited.store.workspace.name, edited.store.is_modified) == ("Lot", True)
    assert len(edited.store.workspace.items) == 2


def _modified_launch(launches: _Launches, tmp_path: Path, prompts: FakePrompts) -> tuple[_Launch, VideoItem]:
    launch = launches.new(prompts)
    clip = _video(tmp_path, "kept.mp4")
    launch.store.add_items([clip])
    return launch, clip


@pytest.mark.parametrize(
    ("choice", "save_to", "replaced"),
    [
        (SaveChoice.CANCEL, None, False),
        (SaveChoice.DISCARD, None, True),
        (SaveChoice.SAVE, "Kept.ax-devil.workspace", True),
        (SaveChoice.SAVE, None, False),  # Cancelling Save As cancels the whole action.
    ],
)
def test_new_workspace_asks_about_unsaved_changes_first(
    launches: _Launches, tmp_path: Path, choice: SaveChoice, save_to: str | None, replaced: bool
) -> None:
    prompts = FakePrompts(choice, save_path=tmp_path / save_to if save_to else None)
    launch, clip = _modified_launch(launches, tmp_path, prompts)

    assert launch.lifecycle.new_workspace() is replaced

    assert prompts.asked_to_save == ["Untitled"]
    assert launch.store.workspace.items == (() if replaced else (clip,))
    if save_to:
        assert load_workspace(tmp_path / save_to).items == (clip,)
    assert launch.store.is_modified is not replaced


@pytest.mark.parametrize(("choice", "opened"), [(SaveChoice.DISCARD, True), (SaveChoice.CANCEL, False)])
def test_opening_a_workspace_asks_about_unsaved_changes_and_adds_its_items_without_resolving_them_as_added(
    launches: _Launches, tmp_path: Path, choice: SaveChoice, opened: bool
) -> None:
    other = save_workspace(Workspace(items=(_video(tmp_path, "other.mp4"),)), tmp_path / "Other.ax-devil.workspace")
    prompts = FakePrompts(choice, open_path=other.path)
    launch, clip = _modified_launch(launches, tmp_path, prompts)
    added = _record_added_flags(launch.store)

    assert launch.lifecycle.open_workspace() is opened

    assert prompts.asked_to_save == ["Untitled"]
    if opened:
        assert launch.store.workspace.items == other.items
        assert (launch.store.workspace.name, launch.store.is_modified) == ("Other", False)
        assert added == [False]
        assert launch.lifecycle.recent_workspaces() == (other.path,)
    else:
        assert launch.store.workspace.items == (clip,)
        assert added == []
        assert launch.lifecycle.recent_workspaces() == ()


def test_cancelling_the_open_dialog_asks_nothing(launches: _Launches, tmp_path: Path) -> None:
    prompts = FakePrompts(SaveChoice.DISCARD, open_path=None)
    launch, clip = _modified_launch(launches, tmp_path, prompts)

    assert not launch.lifecycle.open_workspace()

    assert prompts.asked_to_save == []
    assert launch.store.workspace.items == (clip,)


def test_a_workspace_file_that_cannot_be_read_is_reported_and_changes_nothing(
    launches: _Launches, tmp_path: Path
) -> None:
    damaged = tmp_path / "Damaged.ax-devil.workspace"
    damaged.write_text("{not json")
    prompts = FakePrompts(SaveChoice.DISCARD)
    launch, clip = _modified_launch(launches, tmp_path, prompts)

    assert not launch.lifecycle.open_workspace(damaged)

    [error] = prompts.errors
    assert error.startswith(f"{damaged.name} is not valid JSON")
    assert launch.store.workspace.items == (clip,) and launch.store.is_modified


@pytest.mark.parametrize(("choice", "replaced"), [(SaveChoice.CANCEL, False), (SaveChoice.DISCARD, True)])
def test_a_launch_with_content_starts_a_new_workspace_after_asking_about_the_kept_one(
    launches: _Launches, tmp_path: Path, choice: SaveChoice, replaced: bool
) -> None:
    kept, _ = _modified_launch(launches, tmp_path, FakePrompts())
    kept.lifecycle.keep_workspace()
    prompts = FakePrompts(choice)
    launched = launches.new(prompts)
    added = _record_added_flags(launched.store)
    from_cli = _video(tmp_path, "cli.mp4")

    launched.lifecycle.launch([from_cli])

    assert prompts.asked_to_save == ["Untitled"]
    # The restore resolves the kept item without adding it; only the command-line item counts as added.
    if replaced:
        assert launched.store.workspace.items == (from_cli,)
        assert added == [False, True]
    else:
        assert from_cli not in launched.store.workspace.items and len(launched.store.workspace.items) == 1
        assert added == [False]


def test_a_launch_with_content_replaces_an_unmodified_kept_workspace_without_asking(
    launches: _Launches, tmp_path: Path
) -> None:
    prompts = FakePrompts(SaveChoice.CANCEL)
    launched = launches.new(prompts)
    added = _record_added_flags(launched.store)
    from_cli = _video(tmp_path, "cli.mp4")

    launched.lifecycle.launch([from_cli])

    assert prompts.asked_to_save == []
    assert launched.store.workspace.items == (from_cli,)
    assert added == [True]


def test_a_launch_with_a_workspace_file_opens_it(launches: _Launches, tmp_path: Path) -> None:
    saved = save_workspace(Workspace(items=(_video(tmp_path, "a.mp4"),)), tmp_path / "Lot.ax-devil.workspace")
    launch = launches.new()
    added = _record_added_flags(launch.store)

    launch.lifecycle.launch(workspace_file=saved.path)

    assert launch.store.workspace.items == saved.items
    assert (launch.store.workspace.name, launch.store.is_modified) == ("Lot", False)
    assert added == [False]
    assert launch.lifecycle.recent_workspaces() == (saved.path,)


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
    launch = launches.new(FakePrompts(SaveChoice.DISCARD, save_path=tmp_path / "Lot.ax-devil.workspace"))
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
