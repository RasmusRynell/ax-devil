"""Playlist resolver plugin contract and definitions.

A playlist resolver plugin is responsible for taking user-provided configuration
and resolving it into canonical ``PlaylistContent`` objects that the workspace
and UI can consume directly.

Plugin authors implement one runtime integration surface:

* ``create_settings_widget()`` — returns a PySide6 widget for GUI-driven input.
  The widget must emit ``playlist_resolved(list[PlaylistContent])`` when the
  user is ready.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

import click
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from .base import PluginBase, PluginDefinitionBase

if TYPE_CHECKING:
    from ax_devil.modules.workspace.core import PlaylistContent

PLAYLIST_RESOLVER_PLUGIN_TYPE = "playlist_resolver"


class PlaylistResolverWidget(QWidget):
    """Base widget contract for playlist resolver plugins."""

    playlist_resolved = Signal(object)

    def emit_playlists(self, playlists: list[PlaylistContent]) -> None:
        """Emit resolved playlists to the host dialog."""
        self.playlist_resolved.emit(playlists)


class PlaylistResolverPlugin(PluginBase):
    """Abstract base class for playlist resolver plugins.

    Discovery and registration use classmethods (matching the generic plugin
    framework). Runtime behaviour uses an instance method because the UI widget
    is inherently stateful.
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
    def create_settings_widget(self) -> PlaylistResolverWidget:
        """Return the UI used to collect input and emit resolved playlists."""

    @classmethod
    def create_cli_command(cls) -> click.Command | None:
        """Return the resolver-specific CLI command, if any."""
        return None


__all__ = [
    "PLAYLIST_RESOLVER_PLUGIN_TYPE",
    "PlaylistResolverPlugin",
    "PlaylistResolverWidget",
]
