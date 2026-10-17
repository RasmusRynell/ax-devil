"""The files and folders something was read from, which a watcher checks for changes."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from ax_devil.core.cancellation import CancelToken

MAX_FOLDERS = 4096
"""The most folders one snapshot walks; the rest are left out and the snapshot says so."""


class Fingerprint(NamedTuple):
    """A file's or folder's inode, size and modification time.

    A file's changes whenever it is written or replaced, a folder's whenever an entry in it is added, removed or
    renamed. A tuple, so it can be written as JSON or packed.
    """

    inode: int
    size: int
    mtime_ns: int

    @classmethod
    def of(cls, path: Path) -> Fingerprint | None:
        """Return the fingerprint of *path*, following a symbolic link, or None when it cannot be read."""
        try:
            return cls.of_stat(os.stat(path))
        except OSError:
            return None

    @classmethod
    def of_stat(cls, st: os.stat_result) -> Fingerprint:
        """Return the fingerprint of what the ``stat`` result *st* describes."""
        return cls(st.st_ino, st.st_size, st.st_mtime_ns)


@dataclass(frozen=True, slots=True)
class SourcePaths:
    """Where data was read from.

    *files* are watched together with the folder holding them; *folders* are watched with every subfolder.
    """

    files: frozenset[Path] = frozenset()
    folders: frozenset[Path] = frozenset()

    def __or__(self, other: SourcePaths) -> SourcePaths:
        """Return the paths of both."""
        return SourcePaths(self.files | other.files, self.folders | other.folders)

    def __bool__(self) -> bool:
        """Return whether there is anything to watch."""
        return bool(self.files or self.folders)

    def snapshot(self, token: CancelToken) -> SourceSnapshot:
        """Return what these paths look like on disk now; the one place that reads files and walks folders.

        Each file is fingerprinted, and each folder and every subfolder (symbolic links are not followed) by its own
        modification time, so a big folder costs one read per subfolder. A path that does not exist is absent from
        the fingerprints. At most `MAX_FOLDERS` folders are walked in all. The *token* is checked between folders.
        """
        fingerprints: dict[Path, Fingerprint] = {}
        for file in token.polled(self.files):
            if (fingerprint := Fingerprint.of(file)) is not None:
                fingerprints[file] = fingerprint
        walked = 0
        truncated = False
        for folder in token.polled(self.folders):
            for directory, _subfolders, _names in os.walk(folder):
                token.raise_if_cancelled()
                if walked == MAX_FOLDERS:
                    truncated = True
                    break
                walked += 1
                if (fingerprint := Fingerprint.of(Path(directory))) is not None:
                    fingerprints[Path(directory)] = fingerprint
        return SourceSnapshot(self, fingerprints, truncated)

    @classmethod
    def merged(cls, paths: Iterable[SourcePaths]) -> SourcePaths:
        """Return the paths of all of *paths*."""
        files: set[Path] = set()
        folders: set[Path] = set()
        for each in paths:
            files |= each.files
            folders |= each.folders
        return cls(frozenset(files), frozenset(folders))


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """Source paths and what was on disk at each when they were read.

    *truncated* says that folders beyond `MAX_FOLDERS` were left out.
    """

    paths: SourcePaths
    fingerprints: Mapping[Path, Fingerprint] = field(default_factory=dict)
    truncated: bool = False

    def __or__(self, other: SourceSnapshot) -> SourceSnapshot:
        """Return the snapshot of both; for a path in both, the fingerprint of the left operand wins."""
        return SourceSnapshot(
            self.paths | other.paths,
            {**other.fingerprints, **self.fingerprints},
            self.truncated or other.truncated,
        )
