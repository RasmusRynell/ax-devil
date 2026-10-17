"""The workspace lifecycle: keeping unsaved edits, New/Open/Save, and launches."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from ax_devil.modules.workspace.core import (
    RecentWorkspaces,
    VideoItem,
    Workspace,
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


def test_launch_starts_empty_and_a_quit_with_unsaved_edits_leaves_them_as_a_recent_workspace(
    launches: _Launches, tmp_path: Path
) -> None:
    first = launches.new()
    clip = _video(tmp_path, "lot.mp4")
    first.store.add_items([clip])
    first.store.rename_workspace("Parking lot")
    first.lifecycle.keep_unsaved_changes()

    second = launches.new()
    second.lifecycle.launch()

    assert second.store.workspace == Workspace()
    assert not second.store.is_modified
    [recent] = second.lifecycle.recent_workspaces()
    assert recent.path.parent == tmp_path / "state" / "unsaved-workspaces"
    assert recent.path.name.startswith("Parking lot ")
    assert second.lifecycle.open_workspace(recent.path)
    assert (second.store.workspace.items, second.store.workspace.name) == ((clip,), "Parking lot")


def test_a_quit_keeps_unsaved_edits_of_a_saved_workspace_beside_its_untouched_file(
    launches: _Launches, tmp_path: Path
) -> None:
    saving = launches.new(FakePrompts(save_path=tmp_path / "Lot.ax-devil.workspace"))
    saved_clip, edit = _video(tmp_path, "a.mp4"), _video(tmp_path, "b.mp4")
    saving.store.add_items([saved_clip])
    saving.lifecycle.save_workspace()
    saving.store.add_items([edit])

    saving.lifecycle.keep_unsaved_changes()

    assert load_workspace(tmp_path / "Lot.ax-devil.workspace").items == (saved_clip,)
    [copy, original] = saving.lifecycle.recent_workspaces()
    assert original.path == tmp_path / "Lot.ax-devil.workspace"
    assert load_workspace(copy.path).items == (saved_clip, edit)
    assert saving.store.is_modified


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


@pytest.mark.parametrize(
    ("choice", "save_to", "replaced"),
    [
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


def test_opening_a_workspace_discards_unsaved_changes_without_asking_and_does_not_resolve_items_as_added(
    launches: _Launches, tmp_path: Path
) -> None:
    other = save_workspace(
        Workspace(name="Other", items=(_video(tmp_path, "other.mp4"),)), tmp_path / "Other.ax-devil.workspace"
    )
    prompts = FakePrompts(open_path=other.path)
    launch, _ = _modified_launch(launches, tmp_path, prompts)
    added = _record_added_flags(launch.store)

    assert launch.lifecycle.open_workspace()

    assert prompts.asked_to_save == []
    assert launch.store.workspace.items == other.items
    assert (launch.store.workspace.name, launch.store.is_modified) == ("Other", False)
    assert added == [False]
    assert [entry.path for entry in launch.lifecycle.recent_workspaces()] == [other.path]


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
    prompts = FakePrompts()
    launch, clip = _modified_launch(launches, tmp_path, prompts)

    assert not launch.lifecycle.open_workspace(damaged)

    [error] = prompts.errors
    assert error.startswith(f"{damaged.name} is not valid JSON")
    assert prompts.asked_to_save == []
    assert launch.store.workspace.items == (clip,) and launch.store.is_modified


def test_a_launch_with_content_starts_an_untitled_workspace_with_it_without_asking(
    launches: _Launches, tmp_path: Path
) -> None:
    prompts = FakePrompts()
    launched = launches.new(prompts)
    added = _record_added_flags(launched.store)
    from_cli = _video(tmp_path, "cli.mp4")

    launched.lifecycle.launch([from_cli])

    assert prompts.asked_to_save == []
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
