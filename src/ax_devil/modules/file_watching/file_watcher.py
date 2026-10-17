"""Tell an owner which of its files changed on disk, once the writing has stopped."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from ax_devil.core.cancellation import CancelToken
from ax_devil.core.source_paths import SourcePaths
from ax_devil.modules.background_jobs import BackgroundJobs, background_jobs
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

QUIET_MS = 1000
"""How long the files must stay quiet before they are checked, so one save that writes many times is one change."""

MAX_WATCHED_FOLDERS = 4096
"""The most folders one watcher holds with the operating system; the rest are not noticed."""

_POOL = "file-watching"
_POOL_WORKERS = 2
"""Walking folders may be slow on big trees, so it never shares workers with other background work."""

Fingerprint = tuple[int, int, int]
"""A file's or folder's inode, size and modification time. A file's changes whenever it is written or replaced, a
folder's whenever an entry in it is added, removed or renamed."""


@dataclass(frozen=True, slots=True)
class _Disk:
    """What a worker found: the fingerprints, the paths to watch, and which changes are still being written."""

    fingerprints: dict[Path, Fingerprint]
    files: frozenset[str]
    folders: frozenset[str]
    unsettled: frozenset[Path]
    truncated: bool


@dataclass(slots=True)
class _Registration:
    """One key's paths, the fingerprints last reported on, and the watches its last check asked for."""

    paths: SourcePaths
    baseline: dict[Path, Fingerprint] | None = None  # None until the first check arrives
    baselined: SourcePaths = SourcePaths()  # The paths the baseline covers; ``paths`` may have moved on since.
    files: frozenset[str] = frozenset()
    folders: frozenset[str] = frozenset()
    cancel: Callable[[], None] | None = field(default=None, repr=False)  # The check under way, if any.


class FileWatcher(QObject):
    """Report the registered files that changed, appeared or disappeared, however they were written.

    Each key registers ``SourcePaths``: a file is watched with the folder holding it, a folder with every subfolder.
    Paths that do not exist yet are watched at their nearest existing parent and reported when they appear. Only
    local disks are noticed. Walking and fingerprinting happen on a background pool, never on the GUI thread.

    A folder is watched for which files it holds: its own and its subfolders' fingerprints change when an entry is
    added, removed or renamed, and a file rewritten in place inside it is not noticed. A file whose contents matter
    must be named in ``SourcePaths.files``.

    Every file system event restarts a quiet timer. When it runs out, the registered paths are read again and the
    ones whose fingerprint differs from the last report are emitted once through ``changed``. A file still changing
    between two reads is held back until it stops. Writers that replace a file leave the old watch behind, so
    watches are set again after every check.
    """

    changed = Signal(str, object)  # key, frozenset[Path] of its registered paths that changed

    def __init__(
        self, parent: QObject | None = None, *, quiet_ms: int = QUIET_MS, jobs: BackgroundJobs | None = None
    ) -> None:
        super().__init__(parent)
        self._jobs = (jobs or background_jobs(_POOL, max_workers=_POOL_WORKERS)).scope(self)
        self._settle_s = quiet_ms / 5000  # How long a changed file must stay as it is to count as written.
        self._registrations: dict[str, _Registration] = {}
        self._dirty: set[str] = set()  # Keys with events not checked yet.
        self._capped = False
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._touched)
        self._watcher.directoryChanged.connect(self._touched)
        self._quiet = QTimer(self)
        self._quiet.setSingleShot(True)
        self._quiet.setInterval(quiet_ms)
        self._quiet.timeout.connect(self._check_dirty)

    def watch(self, key: str, paths: SourcePaths) -> None:
        """Report changes to *paths* under *key*, replacing what *key* watched before.

        What exists now is the baseline, taken in the background; changes made before it is taken are not reported.
        Watching other paths under a key keeps its baseline: changes to the paths it already covered are still
        reported, and the paths it did not cover are taken into the baseline by the next check. Empty *paths* watch
        nothing.
        """
        registered = self._registrations.get(key)
        if registered is None:
            if paths:
                registration = self._registrations[key] = _Registration(paths)
                self._start_check(key, registration)
        elif not paths:
            self._drop(key)
            self._sync_watches()
        elif registered.paths != paths:
            registered.paths = paths
            self._start_check(key, registered)

    def unwatch(self, key: str) -> None:
        """Stop reporting changes under *key*."""
        self._drop(key)
        self._sync_watches()

    def clear(self) -> None:
        """Stop reporting anything."""
        for key in tuple(self._registrations):
            self._drop(key)
        self._quiet.stop()
        self._sync_watches()

    def _drop(self, key: str) -> None:
        """Forget *key* and the check under way for it; the watches are left to ``_sync_watches``."""
        registration = self._registrations.pop(key, None)
        self._dirty.discard(key)
        if registration is not None and registration.cancel is not None:
            registration.cancel()

    def _touched(self, path: str) -> None:
        """Note that *path* was touched and wait for the files to go quiet."""
        touched = [
            key
            for key, registration in self._registrations.items()
            if path in registration.files or path in registration.folders
        ]
        if touched:
            self._dirty.update(touched)
            self._quiet.start()

    def _check_dirty(self) -> None:
        """Read the paths of every key that was touched again."""
        dirty, self._dirty = self._dirty, set()
        for key in sorted(dirty):
            registration = self._registrations.get(key)
            if registration is not None and registration.baseline is not None:
                self._start_check(key, registration)

    def _start_check(self, key: str, registration: _Registration) -> None:
        """Read *registration*'s paths in the background; a check still under way is replaced by this one."""
        if registration.cancel is not None:
            registration.cancel()
        paths, baselined = registration.paths, registration.baselined
        baseline = None if registration.baseline is None else dict(registration.baseline)
        settle_s = self._settle_s
        registration.cancel = self._jobs.submit(
            "file watching",
            lambda job: _read_disk(paths, baseline, baselined, settle_s, job.token),
            on_result=partial(self._checked, key, registration, paths),
            on_error=partial(self._check_failed, key, registration),
        )

    def _check_failed(self, key: str, registration: _Registration, error: Exception) -> None:
        """Note that a check failed; the next file system event checks again."""
        if self._registrations.get(key) is registration:
            registration.cancel = None
            logger.warning(f"Could not read watched files: {error}")

    def _checked(self, key: str, registration: _Registration, paths: SourcePaths, disk: _Disk) -> None:
        """Take the baseline, or report the paths that differ from it and are done changing, and watch what exists.

        *paths* are the ones *disk* was read for; a result for paths that were replaced since is dropped.
        """
        if self._registrations.get(key) is not registration or registration.paths != paths:
            return
        registration.cancel = None
        first = registration.baseline is None
        reported: frozenset[Path] = frozenset()
        if registration.baseline is None:
            registration.baseline = disk.fingerprints
        else:
            baselined = registration.baselined
            reported = frozenset(
                _changed_since(registration.baseline, baselined, paths, disk.fingerprints) - disk.unsettled
            )
            baseline = {path: fp for path, fp in registration.baseline.items() if _covers(paths, path)}
            baseline.update((path, fp) for path, fp in disk.fingerprints.items() if not _covers(baselined, path))
            for path in reported:
                if path in disk.fingerprints:
                    baseline[path] = disk.fingerprints[path]
                else:
                    del baseline[path]
            registration.baseline = baseline
        registration.baselined = paths
        registration.files, registration.folders = disk.files, disk.folders
        if disk.truncated:
            self._warn_capped()
        added = self._sync_watches()
        # Look again after the watches were set, which may have been late for a change, or while a file is changing.
        if first or disk.unsettled or not added.isdisjoint(disk.files | disk.folders):
            self._dirty.add(key)
            self._quiet.start()
        if reported:
            self.changed.emit(key, reported)

    def _sync_watches(self) -> set[str]:
        """Watch exactly what the registrations ask for, again the paths a replaced file left; return what was added."""
        folders: set[str] = set()
        files: set[str] = set()
        for registration in self._registrations.values():
            folders |= registration.folders
            files |= registration.files
        if len(folders) > MAX_WATCHED_FOLDERS:
            self._warn_capped()
            folders = set(sorted(folders)[:MAX_WATCHED_FOLDERS])
        desired = folders | files
        current = set(self._watcher.files()) | set(self._watcher.directories())
        if current - desired:
            self._watcher.removePaths(sorted(current - desired))
        missing = desired - current
        if not missing:
            return set()
        return missing - set(self._watcher.addPaths(sorted(missing)))

    def _warn_capped(self) -> None:
        """Say once that folders beyond the limit are not watched."""
        if not self._capped:
            self._capped = True
            logger.warning(f"Watching only {MAX_WATCHED_FOLDERS} folders; changes in the others are not noticed")


def _differences(before: Mapping[Path, Fingerprint], after: Mapping[Path, Fingerprint]) -> set[Path]:
    """Return the paths that were added, removed or changed from *before* to *after*."""
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}


def _covers(paths: SourcePaths, path: Path) -> bool:
    """Return whether *path* is one of the files of *paths*, or a folder of it or inside one."""
    return path in paths.files or any(path.is_relative_to(folder) for folder in paths.folders)


def _changed_since(
    baseline: Mapping[Path, Fingerprint], baselined: SourcePaths, paths: SourcePaths, after: Mapping[Path, Fingerprint]
) -> set[Path]:
    """Return what differs between *baseline* and *after* among the paths both *baselined* and *paths* cover."""
    before = {path: fp for path, fp in baseline.items() if _covers(paths, path)}
    return _differences(before, {path: fp for path, fp in after.items() if _covers(baselined, path)})


def _fingerprint(path: Path) -> Fingerprint | None:
    """Return *path*'s fingerprint, following a symbolic link, or None when it cannot be read."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def _nearest_existing(path: Path) -> str:
    """Return *path*, or its nearest parent that exists, as a clean string."""
    while not path.exists() and path != path.parent:
        path = path.parent
    return os.path.normpath(path)


def _read_disk(
    paths: SourcePaths,
    baseline: Mapping[Path, Fingerprint] | None,
    baselined: SourcePaths,
    settle_s: float,
    token: CancelToken,
) -> _Disk:
    """Fingerprint the files of *paths* and every folder walked, without following symbolic links; say what to watch.

    A walked folder is fingerprinted, not the files in it, so a big folder costs one read per folder. With a
    *baseline* covering *baselined*, the paths that differ from it are read again after *settle_s*; those that
    changed meanwhile are still being written.
    """
    fingerprints: dict[Path, Fingerprint] = {}
    files: set[str] = set()
    folders: set[str] = set()
    truncated = False
    for file in token.polled(paths.files):
        fingerprint = _fingerprint(file)
        if fingerprint is None:
            folders.add(_nearest_existing(file.parent))
        else:
            fingerprints[file] = fingerprint
            files.add(os.path.normpath(file))
            folders.add(os.path.normpath(file.parent))
    for folder in token.polled(paths.folders):
        if not folder.is_dir():
            folders.add(_nearest_existing(folder.parent))
            continue
        walked = 0
        for directory, subfolders, _names in os.walk(folder):
            token.raise_if_cancelled()
            folders.add(os.path.normpath(directory))
            walked += 1
            if walked >= MAX_WATCHED_FOLDERS:
                truncated = truncated or bool(subfolders)
                subfolders.clear()
            if (fingerprint := _fingerprint(Path(directory))) is not None:
                fingerprints[Path(directory)] = fingerprint
    unsettled: set[Path] = set()
    if baseline is not None and (differing := _changed_since(baseline, baselined, paths, fingerprints)):
        token.wait(settle_s)
        unsettled = {path for path in differing if _fingerprint(path) != fingerprints.get(path)}
    return _Disk(fingerprints, frozenset(files), frozenset(folders), frozenset(unsettled), truncated)
