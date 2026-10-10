"""The workspace lifecycle: keeping and restoring, New/Open/Save with the modified prompt, launches, and rename."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.workspace.core import (
    RecentWorkspaces,
    VideoItem,
    Workspace,
    WorkspaceBackup,
    load_workspace,
    save_workspace,
)
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from ax_devil.modules.workspace.ui.workspace_prompts import SaveChoice
from tests.helpers.workspace import DummyViewer, FakePrompts, FakeResolutionContext


@pytest.fixture(autouse=True)
def _dummy_viewers() -> Iterator[None]:
    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        yield


class _Sessions:
    """Build sessions that share one storage folder, as consecutive launches of the app do."""

    def __init__(self, qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path) -> None:
        self._qtbot = qtbot
        self._render_catalog_manager = render_catalog_manager
        self.state = tmp_path / "state"

    def new(self, prompts: FakePrompts | None = None) -> WorkspaceSession:
        session = WorkspaceSession(
            render_catalog_manager=self._render_catalog_manager,
            context=FakeResolutionContext(),
            backup=WorkspaceBackup(lambda: self.state / "workspace-backup.json"),
            recent_workspaces=RecentWorkspaces(lambda: self.state / "recent-workspaces.json"),
            prompts=prompts or FakePrompts(),
        )
        self._qtbot.addWidget(session.widget())
        return session


@pytest.fixture()
def sessions(qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path) -> _Sessions:
    return _Sessions(qtbot, render_catalog_manager, tmp_path)


def _video(tmp_path: Path, name: str) -> VideoItem:
    path = tmp_path / name
    path.write_bytes(b"")
    return VideoItem(video=path)


def _items(session: WorkspaceSession) -> tuple[object, ...]:
    return session._workspace_store.workspace.items


def _viewer_count(session: WorkspaceSession) -> int:
    return session._center_area.get_widget_count()


def _row_count(session: WorkspaceSession) -> int:
    return session._content_browser._tree.topLevelItemCount()


def test_closing_keeps_an_untitled_workspace_and_launch_lists_it_without_opening_anything(
    sessions: _Sessions, tmp_path: Path
) -> None:
    first = sessions.new()
    clip = _video(tmp_path, "lot.mp4")
    first.add_items([clip])
    assert _viewer_count(first) == 1
    first.keep_workspace()

    second = sessions.new()
    second.launch()

    assert _items(second) == (clip,)
    assert _row_count(second) == 1
    assert _viewer_count(second) == 0
    assert (second.workspace_name, second.is_modified) == ("Untitled", True)


def test_saving_an_untitled_workspace_asks_where_appends_the_extension_and_remembers_it(
    sessions: _Sessions, tmp_path: Path
) -> None:
    prompts = FakePrompts(save_path=tmp_path / "reviews" / "Lot")
    session = sessions.new(prompts)
    session.add_items([_video(tmp_path, "lot.mp4")])

    assert session.save_workspace()

    saved = tmp_path / "reviews" / "Lot.ax-devil.workspace"
    assert load_workspace(saved).items == _items(session)
    assert (session.workspace_name, session.is_modified) == ("Lot", False)
    assert session.recent_workspaces() == (saved,)
    assert session._welcome._recent_workspaces == (saved,)


def test_a_restored_saved_workspace_is_modified_only_when_it_had_unsaved_edits(
    sessions: _Sessions, tmp_path: Path
) -> None:
    session = sessions.new(FakePrompts(save_path=tmp_path / "Lot.ax-devil.workspace"))
    session.add_items([_video(tmp_path, "a.mp4")])
    session.save_workspace()
    session.keep_workspace()

    unchanged = sessions.new()
    unchanged.launch()
    assert (unchanged.workspace_name, unchanged.is_modified) == ("Lot", False)

    unchanged.add_items([_video(tmp_path, "b.mp4")])
    unchanged.keep_workspace()
    edited = sessions.new()
    edited.launch()
    assert (edited.workspace_name, edited.is_modified) == ("Lot", True)
    assert len(_items(edited)) == 2


def _modified_session(sessions: _Sessions, tmp_path: Path, prompts: FakePrompts) -> tuple[WorkspaceSession, VideoItem]:
    session = sessions.new(prompts)
    clip = _video(tmp_path, "kept.mp4")
    session.add_items([clip])
    return session, clip


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
    sessions: _Sessions, tmp_path: Path, choice: SaveChoice, save_to: str | None, replaced: bool
) -> None:
    prompts = FakePrompts(choice, save_path=tmp_path / save_to if save_to else None)
    session, clip = _modified_session(sessions, tmp_path, prompts)

    assert session.new_workspace() is replaced

    assert prompts.asked_to_save == ["Untitled"]
    assert _items(session) == (() if replaced else (clip,))
    assert _viewer_count(session) == (0 if replaced else 1)
    if save_to:
        assert load_workspace(tmp_path / save_to).items == (clip,)
    assert session.is_modified is not replaced


@pytest.mark.parametrize(("choice", "opened"), [(SaveChoice.DISCARD, True), (SaveChoice.CANCEL, False)])
def test_opening_a_workspace_asks_about_unsaved_changes_and_opens_nothing_on_screen(
    sessions: _Sessions, tmp_path: Path, choice: SaveChoice, opened: bool
) -> None:
    other = save_workspace(Workspace(items=(_video(tmp_path, "other.mp4"),)), tmp_path / "Other.ax-devil.workspace")
    prompts = FakePrompts(choice, open_path=other.path)
    session, clip = _modified_session(sessions, tmp_path, prompts)

    assert session.open_workspace() is opened

    assert prompts.asked_to_save == ["Untitled"]
    if opened:
        assert _items(session) == other.items
        assert (session.workspace_name, session.is_modified) == ("Other", False)
        assert _viewer_count(session) == 0 and _row_count(session) == 1
        assert session.recent_workspaces() == (other.path,)
    else:
        assert _items(session) == (clip,)
        assert session.recent_workspaces() == ()


def test_cancelling_the_open_dialog_asks_nothing(sessions: _Sessions, tmp_path: Path) -> None:
    prompts = FakePrompts(SaveChoice.DISCARD, open_path=None)
    session, clip = _modified_session(sessions, tmp_path, prompts)

    assert not session.open_workspace()

    assert prompts.asked_to_save == []
    assert _items(session) == (clip,)


def test_a_workspace_file_that_cannot_be_read_is_reported_and_changes_nothing(
    sessions: _Sessions, tmp_path: Path
) -> None:
    damaged = tmp_path / "Damaged.ax-devil.workspace"
    damaged.write_text("{not json")
    prompts = FakePrompts(SaveChoice.DISCARD)
    session, clip = _modified_session(sessions, tmp_path, prompts)

    assert not session.open_workspace(damaged)

    [error] = prompts.errors
    assert error.startswith(f"{damaged.name} is not valid JSON")
    assert _items(session) == (clip,) and session.is_modified


@pytest.mark.parametrize(("choice", "replaced"), [(SaveChoice.CANCEL, False), (SaveChoice.DISCARD, True)])
def test_a_launch_with_content_starts_a_new_workspace_after_asking_about_the_kept_one(
    sessions: _Sessions, tmp_path: Path, choice: SaveChoice, replaced: bool
) -> None:
    kept, _ = _modified_session(sessions, tmp_path, FakePrompts())
    kept.keep_workspace()
    prompts = FakePrompts(choice)
    launched = sessions.new(prompts)
    from_cli = _video(tmp_path, "cli.mp4")

    launched.launch([from_cli])

    assert prompts.asked_to_save == ["Untitled"]
    if replaced:
        assert _items(launched) == (from_cli,)
        assert _viewer_count(launched) == 1, "the first item from the command line opens"
    else:
        assert from_cli not in _items(launched) and len(_items(launched)) == 1
        assert _viewer_count(launched) == 0


def test_a_launch_with_content_replaces_an_unmodified_kept_workspace_without_asking(
    sessions: _Sessions, tmp_path: Path
) -> None:
    prompts = FakePrompts(SaveChoice.CANCEL)
    launched = sessions.new(prompts)
    from_cli = _video(tmp_path, "cli.mp4")

    launched.launch([from_cli])

    assert prompts.asked_to_save == []
    assert _items(launched) == (from_cli,)


def test_a_launch_with_a_workspace_file_opens_it(sessions: _Sessions, tmp_path: Path) -> None:
    saved = save_workspace(Workspace(items=(_video(tmp_path, "a.mp4"),)), tmp_path / "Lot.ax-devil.workspace")
    session = sessions.new()

    session.launch(workspace_file=saved.path)

    assert _items(session) == saved.items
    assert (session.workspace_name, session.is_modified) == ("Lot", False)
    assert session.recent_workspaces() == (saved.path,)


def test_renaming_an_item_updates_its_rows_and_open_viewers_and_an_empty_name_restores_the_default(
    sessions: _Sessions, tmp_path: Path
) -> None:
    session = sessions.new()
    clip = _video(tmp_path, "lot.mp4")
    session.add_items([clip])
    viewer = cast(DummyViewer, session.focused_widget())
    browser = session._content_browser

    browser.item_rename_requested.emit(clip.id, "North gate")

    renamed = session._workspace_store.workspace.item(clip.id)
    assert renamed.label == "North gate"
    assert viewer._title_label.text() == "North gate"
    assert browser._tree.topLevelItem(0).text(0) == "North gate"  # type: ignore[union-attr]
    assert session.is_modified

    browser.item_rename_requested.emit(clip.id, "")

    assert session._workspace_store.workspace.item(clip.id).label == ""
    assert viewer._title_label.text() == "lot.mp4"


@pytest.mark.parametrize("replace", [False, True])
def test_save_as_asks_before_replacing_the_file_the_appended_extension_names(
    sessions: _Sessions, tmp_path: Path, replace: bool
) -> None:
    existing = save_workspace(Workspace(), tmp_path / "Lot.ax-devil.workspace")
    prompts = FakePrompts(save_path=tmp_path / "Lot", replace=replace)
    session = sessions.new(prompts)
    clip = _video(tmp_path, "lot.mp4")
    session.add_items([clip])

    assert session.save_workspace_as() is replace

    assert prompts.asked_to_replace == [existing.path]
    assert load_workspace(tmp_path / "Lot.ax-devil.workspace").items == ((clip,) if replace else ())
    assert session.is_modified is not replace


def test_opening_a_workspace_closes_every_viewer_even_of_items_it_shares(sessions: _Sessions, tmp_path: Path) -> None:
    """Reopening the file an open viewer's item came from leaves no viewer with Content from before."""
    session = sessions.new(FakePrompts(SaveChoice.DISCARD, save_path=tmp_path / "Lot.ax-devil.workspace"))
    session.add_items([_video(tmp_path, "lot.mp4")])
    session.save_workspace()
    assert _viewer_count(session) == 1

    assert session.open_workspace(tmp_path / "Lot.ax-devil.workspace")

    assert _viewer_count(session) == 0
    assert _row_count(session) == 1


def test_kept_workspace_goes_to_the_storage_folder_saved_after_launch(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    config = ConfigManager()

    def storage(folder: str) -> dict[str, str]:
        root = tmp_path / folder
        return {
            "base_dir": str(root),
            "cache_dir": str(root / "caches"),
            "logs_dir": str(root / "logs"),
            "render_catalogs_dir": str(root / "render_catalogs"),
        }

    config.set("storage", storage("launched"))
    config.activate_storage()
    config.set("storage", storage("changed"))
    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager, context=FakeResolutionContext(), prompts=FakePrompts()
    )
    qtbot.addWidget(session.widget())

    session.keep_workspace()

    assert (tmp_path / "changed" / "workspace-backup.json").is_file()
    assert not (tmp_path / "launched" / "workspace-backup.json").exists()
