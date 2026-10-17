"""Playlist resolver plugin contract and definitions.

A playlist resolver turns JSON-serializable settings into ``PlaylistContent``. Settings are what a Playlist Item saves,
so the resolver runs again, without any widget, every time the item resolves.

Plugin authors implement:

* ``resolve(settings, token)`` — headless; returns the playlists the settings describe and raises ``ValueError`` or
  ``OSError`` with a user-facing message when the settings are missing, invalid, or point at nothing. It runs on a
  worker thread, possibly beside another call, so it touches no Qt objects and no shared mutable state.
* ``watched_folders(settings)`` — optional; the folders the playlists are read from, so adding or removing files there
  resolves them again. Pure: it opens no file and lists no folder.
* ``create_settings_widget()`` — returns a widget that edits settings and calls ``submit_settings(settings)`` when
  the user is ready. Work that would freeze the form, such as scanning a folder, runs through the widget's ``jobs``.
* ``create_cli_command()`` — optional; builds settings from CLI arguments and passes a ``PlaylistItem`` to the runner
  in ``ctx.obj["run_with_items"]``.
"""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import click
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.background_jobs import JobScope, background_jobs

from .base import PluginBase

if TYPE_CHECKING:
    from ax_devil.modules.workspace.core import PlaylistContent, PlaylistSettings

PLAYLIST_RESOLVER_PLUGIN_TYPE = "playlist_resolver"


class PlaylistResolverWidget(QWidget):
    """Base widget contract for playlist resolver plugins: it edits settings and submits them."""

    settings_submitted = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Finite work away from the GUI thread: results arrive on it, and none after the widget is destroyed. A pool of
        # its own, so a slow folder scan never holds up scans, discovery or item resolution.
        self.jobs: JobScope = background_jobs("plugins").scope(self)

    def submit_settings(self, settings: PlaylistSettings) -> None:
        """Hand the edited settings to the host dialog."""
        self.settings_submitted.emit(settings)


class PlaylistResolverPlugin(PluginBase):
    """Abstract base class for playlist resolver plugins.

    Discovery and registration use classmethods (matching the generic plugin framework). Resolving and the settings
    widget are instance methods; the host creates an instance for each use.
    """

    @classmethod
    def plugin_type(cls) -> str:
        """Return the generic plugin category for playlist resolver bundles."""
        return PLAYLIST_RESOLVER_PLUGIN_TYPE

    # -- Instance methods for runtime integration ---------------------------

    @abstractmethod
    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        """Return the playlists *settings* describe; raise ``ValueError`` or ``OSError`` naming what is wrong."""

    def watched_folders(self, settings: PlaylistSettings) -> list[Path]:
        """Return the folders the playlists *settings* describe are read from; the default is none.

        Pure: it opens no file and lists no folder, since it runs on the GUI thread. The files the resolved playlists
        name, and the folders holding them, are watched whatever this returns.
        """
        return []

    @abstractmethod
    def create_settings_widget(self) -> PlaylistResolverWidget:
        """Return the UI that edits settings and submits them."""

    @classmethod
    def create_cli_command(cls) -> click.Command | None:
        """Return the resolver-specific CLI command, if any."""
        return None


__all__ = [
    "PLAYLIST_RESOLVER_PLUGIN_TYPE",
    "PlaylistResolverPlugin",
    "PlaylistResolverWidget",
]
