"""What launching, quitting, New, Open, Save, and Save As do to the current Workspace."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QTimer, Signal

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import (
    RecentWorkspace,
    RecentWorkspaces,
    Workspace,
    WorkspaceFileError,
    WorkspaceItem,
    read_recovery,
    workspace_file_path,
    write_recovery,
)
from ax_devil.modules.workspace.ui.workspace_prompts import WorkspacePrompts
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

logger = get_logger(__name__)

_COPY_STEM_BYTES = 64  # The name in a copy's file name is a label; this leaves ample room under any NAME_MAX.
_COPY_FALLBACK_STEM = "Unsaved workspace"
_SYNC_INTERVAL_MS = 2000


class _CopyInUse(OSError):
    """A recovery copy is held by another running instance."""


def _normalized(path: Path) -> Path:
    return Path(os.path.normpath(path.absolute()))


class WorkspaceLifecycle(QObject):
    """Decide when the Workspace in *store* is replaced, saved, kept, and remembered; build no widgets.

    Nothing here asks about unsaved edits. A Workspace with unsaved edits has one recovery copy in the folder
    *unsaved_folder* names, rewritten in place every couple of seconds while the Workspace changes and deleted once
    it is saved or discarded. The copy is listed in *recent_workspaces* like any opened or saved file and is kept
    when the Workspace is replaced or the app quits. A launch with nothing to open restores the newest recent
    workspace if it is a copy. An instance holds a lock on its copy, so no other instance restores it.
    """

    recent_workspaces_changed = Signal()
    recovered = Signal()  # A recovery copy replaced the Workspace; its edits are unsaved.

    def __init__(
        self,
        store: WorkspaceStore,
        prompts: WorkspacePrompts,
        unsaved_folder: Callable[[], Path],
        recent_workspaces: RecentWorkspaces,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._prompts = prompts
        self._unsaved_folder = unsaved_folder
        self._recent_workspaces = recent_workspaces
        self._recovery: Path | None = None  # This instance's copy of the Workspace's unsaved edits, once written.
        self._lock: QLockFile | None = None
        self._listed = False  # Whether the copy is among the recent workspaces, so a crash leaves it findable.
        # A Workspace is immutable, so any edit makes a new one: polling its identity needs no signal from the store.
        self._synced: Workspace | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(_SYNC_INTERVAL_MS)
        self._timer.timeout.connect(self._sync_recovery)
        self._timer.start()

    def recent_workspaces(self) -> tuple[RecentWorkspace, ...]:
        """Return the recently opened or saved workspace files, newest first; their files are not checked."""
        return self._recent_workspaces.entries()

    def launch(self, items: Sequence[WorkspaceItem] = (), workspace_file: Path | None = None) -> None:
        """Open *workspace_file* or start a new Workspace with *items*, if given; otherwise restore the unsaved edits
        the last run left, or stay empty."""
        if workspace_file is not None:
            self.open_workspace(workspace_file)
        elif items:
            self.new_workspace(items)
        else:
            self._restore_newest_recent()

    def keep_unsaved_changes(self) -> None:
        """Bring the recovery copy up to date and list it first among the recent workspaces.

        Called when quitting and before the Workspace is replaced. A failure is logged, since neither may be stopped
        by it.
        """
        self._sync_recovery()
        if self._recovery is not None:
            self._listed = self._remember(self._recovery)

    def new_workspace(self, items: Sequence[WorkspaceItem] = ()) -> None:
        """Replace the Workspace with a new Untitled one holding *items*; unsaved edits stay in their copy."""
        self.keep_unsaved_changes()
        self._store.replace_workspace(Workspace())
        self._detach_recovery()
        self._store.add_items(items)

    def open_workspace(self, path: Path | None = None) -> bool:
        """Open the workspace saved in *path*, or in a file the user picks; unsaved edits stay in their copy.

        A recovery copy is restored with its edits unsaved. Return whether it was opened.
        """
        path = path or self._prompts.choose_workspace_to_open()
        if path is None:
            return False
        self.keep_unsaved_changes()
        try:
            if self._is_recovery_copy(path):
                self._restore(path)
            else:
                self._store.open_workspace(path)
                self._detach_recovery()
        except _CopyInUse:
            self._prompts.show_error(f"{path.name} is open in another window.")
            return False
        except (WorkspaceFileError, OSError) as exc:
            logger.warning(f"Could not open workspace {path}: {exc}")
            self._recent_workspaces.forget(path)
            self.recent_workspaces_changed.emit()
            self._prompts.show_error(str(exc))
            return False
        self._remember(path)
        return True

    def revert_workspace(self) -> bool:
        """Discard the unsaved edits: reopen the Workspace's own file, or start an empty Untitled one.

        Only once that has succeeded is the recovery copy deleted and forgotten. Return whether it succeeded.
        """
        path = self._store.workspace.path
        try:
            if path is None:
                self._store.replace_workspace(Workspace())
            else:
                self._store.open_workspace(path)
        except (WorkspaceFileError, OSError) as exc:
            logger.warning(f"Could not reopen workspace {path}: {exc}")
            self._prompts.show_error(str(exc))
            return False
        self._delete_recovery()
        if path is not None:
            self._remember(path)
        return True

    def save_workspace(self) -> bool:
        """Save the Workspace to its file, or ask where when it has none; return whether it was saved."""
        path = self._store.workspace.path
        return self.save_workspace_as() if path is None else self._save_to(path)

    def save_workspace_as(self) -> bool:
        """Save the Workspace to a file the user picks; return whether it was saved.

        The extension is appended when the chosen name lacks it; the dialog did not check that file, so replacing it
        is asked here.
        """
        workspace = self._store.workspace
        chosen = self._prompts.choose_save_path(
            workspace.path or workspace_file_path(Path.home() / workspace.suggested_file_stem)
        )
        if chosen is None:
            return False
        path = workspace_file_path(chosen)
        if path != chosen and path.exists() and not self._prompts.confirm_replace(path):
            return False
        return self._save_to(path)

    def _save_to(self, path: Path) -> bool:
        """Save the Workspace to *path*, telling the user when that fails."""
        try:
            self._store.save_workspace(path)
        except (WorkspaceFileError, OSError) as exc:
            logger.warning(f"Could not save workspace to {path}: {exc}")
            self._prompts.show_error(str(exc))
            return False
        self._sync_recovery()
        self._remember(path)
        return True

    def _restore_newest_recent(self) -> None:
        """Restore the newest recent workspace if it is a recovery copy; one that fails is forgotten, silently."""
        recent = self._recent_workspaces.entries()
        if not recent or not self._is_recovery_copy(recent[0].path):
            return
        copy = recent[0].path
        try:
            self._restore(copy)
        except _CopyInUse:
            logger.info(f"Not restoring {copy}: another instance holds it.")
        except (WorkspaceFileError, OSError) as exc:
            logger.warning(f"Could not restore unsaved changes from {copy}: {exc}")
            self._recent_workspaces.forget(copy)
            self.recent_workspaces_changed.emit()

    def _is_recovery_copy(self, path: Path) -> bool:
        return _normalized(path).is_relative_to(_normalized(self._unsaved_folder()))

    def _restore(self, copy: Path) -> None:
        """Make the recovery copy *copy* the Workspace, with its edits unsaved, and this instance's copy.

        Raises ``_CopyInUse`` when another instance holds it, and ``WorkspaceFileError`` or ``OSError`` when it cannot
        be read; the Workspace is then unchanged.
        """
        copy = _normalized(copy)
        lock = None if copy == self._recovery else self._lock_copy(copy)
        try:
            self._store.replace_workspace(read_recovery(copy), unsaved=True)
        except (WorkspaceFileError, OSError):
            if lock is not None:
                lock.unlock()
            raise
        if lock is not None:
            self._detach_recovery()
            self._recovery, self._lock, self._listed = copy, lock, True
        self._synced = self._store.workspace
        self.recovered.emit()

    @staticmethod
    def _lock_copy(copy: Path) -> QLockFile:
        """Lock *copy* for this instance, or raise ``_CopyInUse``.

        A lock is stale only when its process is gone: one held by a long-running instance never expires.
        """
        lock = QLockFile(f"{copy}.lock")
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise _CopyInUse(f"{copy} is in use")
        return lock

    def _sync_recovery(self) -> None:
        """Bring the recovery copy up to date: written while the Workspace has unsaved edits, deleted when it has not.

        Nothing happens while the Workspace is the one the copy was last synced with. A failure is logged and leaves
        the Workspace unsynced, so the next tick tries again.
        """
        workspace = self._store.workspace
        if workspace is self._synced:
            return
        done = self._write_recovery(workspace) if self._store.is_modified else self._delete_recovery()
        if done:
            self._synced = workspace

    def _write_recovery(self, workspace: Workspace) -> bool:
        """Write *workspace* to this instance's copy, claiming a new file the first time, and list it.

        Return whether the copy is written and listed.
        """
        copy, lock = self._recovery, self._lock
        try:
            if copy is None:
                copy = _normalized(self._reserve_copy_path(workspace))
                lock = self._lock_copy(copy)
            write_recovery(workspace, copy)
        except (WorkspaceFileError, OSError) as exc:
            logger.warning(
                f"Could not keep the unsaved workspace {workspace.name!r} in {self._unsaved_folder()}: {exc}"
            )
            if self._recovery is None and copy is not None:
                if lock is not None:
                    lock.unlock()
                copy.unlink(missing_ok=True)
            return False
        if self._recovery is None:
            self._recovery, self._lock = copy, lock
        if not self._listed:
            self._listed = self._remember(copy)  # Unlisted, the copy is still on disk if this fails.
        return self._listed

    def _detach_recovery(self) -> None:
        """Stop treating the copy as this instance's, leaving it on disk and among the recent workspaces."""
        if self._lock is not None:
            self._lock.unlock()
        self._recovery = self._lock = None
        self._listed = False

    def _delete_recovery(self) -> bool:
        """Delete the copy, if there is one, and forget it; return whether it is gone.

        A copy that cannot be deleted stays this instance's, locked, to be deleted by a later sync.
        """
        copy = self._recovery
        if copy is None:
            return True
        try:
            copy.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"Could not delete the recovery copy {copy}: {exc}")
            return False
        self._detach_recovery()
        self._recent_workspaces.forget(copy)
        self.recent_workspaces_changed.emit()
        return True

    def _reserve_copy_path(self, workspace: Workspace) -> Path:
        """Create and return an empty file named after *workspace* and now that no other file or instance has.

        The name is only a label: it is encoded with unencodable characters replaced and cut short, and when the file
        system still refuses the file, a name without it is used, so the copy is made whenever the folder is writable.
        """
        folder = self._unsaved_folder()
        folder.mkdir(parents=True, exist_ok=True)
        label = workspace.suggested_file_stem.encode(errors="replace")[:_COPY_STEM_BYTES].decode(errors="ignore")
        now = f"{datetime.now():%Y-%m-%d %H-%M-%S}"
        try:
            return self._create_unique_file(folder, f"{label} {now}")
        except OSError as exc:
            logger.warning(f"Could not create a recovery file named after {label!r}, using a plain name: {exc}")
            return self._create_unique_file(folder, f"{_COPY_FALLBACK_STEM} {now}")

    @staticmethod
    def _create_unique_file(folder: Path, stem: str) -> Path:
        """Create an empty workspace file named *stem*, with a number added when taken, and return its path.

        Creating the file is what claims the name, so instances saving at the same moment get different files.
        """
        number = 1
        while True:
            path = workspace_file_path(folder / (stem if number == 1 else f"{stem} {number}"))
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            except FileExistsError:
                number += 1
            else:
                return path

    def _remember(self, path: Path) -> bool:
        """Record *path* as the most recent workspace file; return whether the list could be saved."""
        recorded = self._recent_workspaces.record(path)
        self.recent_workspaces_changed.emit()
        return recorded
