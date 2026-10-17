"""The files and folders something was read from, which a watcher checks for changes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


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

    @classmethod
    def merged(cls, paths: Iterable[SourcePaths]) -> SourcePaths:
        """Return the paths of all of *paths*."""
        files: set[Path] = set()
        folders: set[Path] = set()
        for each in paths:
            files |= each.files
            folders |= each.folders
        return cls(frozenset(files), frozenset(folders))
