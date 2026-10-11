"""Playlist resolver plugin contract and definitions.

A playlist resolver turns JSON-serializable settings into ``PlaylistContent``. Settings are what a Playlist Item saves,
so the resolver runs again, without any widget, every time the item resolves.

Plugin authors implement:

* ``resolve(settings)`` — headless; returns the playlists the settings describe and raises ``ValueError`` or
  ``OSError`` with a user-facing message when the settings are missing, invalid, or point at nothing. It runs on a
  worker thread, possibly beside another call, so it touches no Qt objects and no shared mutable state.
* ``create_settings_widget()`` — returns a widget that edits settings and calls ``submit_settings(settings)`` when
  the user is ready.
* ``create_cli_command()`` — optional; builds settings from CLI arguments and passes a ``PlaylistItem`` to the runner
  in ``ctx.obj["run_with_items"]``.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

import click
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from .base import PluginBase, PluginDefinitionBase

if TYPE_CHECKING:
    from ax_devil.modules.workspace.core import PlaylistContent, PlaylistSettings

PLAYLIST_RESOLVER_PLUGIN_TYPE = "playlist_resolver"


class PlaylistResolverWidget(QWidget):
    """Base widget contract for playlist resolver plugins: it edits settings and submits them."""

    settings_submitted = Signal(object)

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

    @classmethod
    def definition(cls) -> PluginDefinitionBase:
        """Bundle playlist resolver metadata into a single definition object."""
        return PluginDefinitionBase(
            plugin_type=cls.plugin_type(),
            plugin_id=cls.plugin_id(),
            display_name=cls.display_name(),
            description=cls.description(),
        )

    # -- Instance methods for runtime integration ---------------------------

    @abstractmethod
    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
        """Return the playlists *settings* describe; raise ``ValueError`` or ``OSError`` naming what is wrong."""

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
