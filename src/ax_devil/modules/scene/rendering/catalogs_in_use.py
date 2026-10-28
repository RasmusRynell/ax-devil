"""The catalog each open view shows, so a view can say which others differ."""

from __future__ import annotations

from collections.abc import Callable
from itertools import count
from pathlib import Path


class CatalogsInUse:
    """Which catalog path each open view shows, in the order the views opened.

    A view adds itself, updates its path as the user chooses, and discards itself when it closes. *changed* is called
    after every real change.
    """

    def __init__(self, changed: Callable[[], None]) -> None:
        """Create an empty registry that calls *changed* when its contents change."""
        self._changed = changed
        self._keys = count()
        self._paths: dict[int, Path] = {}

    def add(self, path: Path) -> int:
        """Register a view showing *path* and return its key."""
        key = next(self._keys)
        self._paths[key] = path
        self._changed()
        return key

    def set_path(self, key: int, path: Path) -> None:
        """Record that the view *key* now shows *path*."""
        if key in self._paths and self._paths[key] != path:
            self._paths[key] = path
            self._changed()

    def discard(self, key: int) -> None:
        """Forget the view *key*; forgetting one already gone does nothing."""
        if self._paths.pop(key, None) is not None:
            self._changed()

    def paths(self) -> tuple[Path, ...]:
        """Return one path per open view, in opening order."""
        return tuple(self._paths.values())
