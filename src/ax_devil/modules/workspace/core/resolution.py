"""What resolving a Workspace Item needs: the intake, playlist resolvers, and the error it raises."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ax_devil.core.cancellation import CancelToken
from ax_devil.core.source_paths import SourcePaths
from ax_devil.modules.workspace.core.content import PlaylistContent
from ax_devil.modules.workspace.core.intake import WorkspaceIntake

PlaylistSettings = Mapping[str, Any]
"""A playlist resolver's settings: a JSON-serializable mapping of strings, numbers, lists, and mappings."""


class ItemResolutionError(Exception):
    """A Workspace Item could not be resolved; the message is the user-facing reason."""


class PlaylistResolver(Protocol):
    """The headless part of a playlist resolver plugin."""

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        """Return the playlists *settings* describe; raise ``ValueError`` or ``OSError`` naming what is wrong."""
        ...

    def source_paths(self, settings: PlaylistSettings, token: CancelToken) -> SourcePaths:
        """Return the files and folders the playlists *settings* describe are read from, so a change to them resolves
        the item again."""
        ...


class ResolutionContext(Protocol):
    """Services an item uses to rebuild its Content."""

    @property
    def intake(self) -> WorkspaceIntake:
        """Return the intake that validates selections and constructs video Content."""
        ...

    def playlist_resolver(self, resolver_id: str) -> PlaylistResolver:
        """Return the installed resolver with *resolver_id*, or raise ``ItemResolutionError`` when there is none."""
        ...
