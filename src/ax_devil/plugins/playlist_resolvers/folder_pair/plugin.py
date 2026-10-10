"""Folder-pair playlist resolver plugin."""

from __future__ import annotations

from pathlib import Path

import click

from ax_devil.modules.plugin_system import PlaylistResolverPlugin, PlaylistResolverWidget
from ax_devil.modules.workspace.core import PlaylistContent, PlaylistItem, PlaylistSettings
from ax_devil.plugins.playlist_resolvers.folder_pair.resolver import resolve_settings


class FolderPairResolverPlugin(PlaylistResolverPlugin):
    """Resolve a playlist from separate video and overlay folders."""

    @classmethod
    def plugin_id(cls) -> str:
        return "folder_pair"

    @classmethod
    def display_name(cls) -> str:
        return "Folder Pair"

    @classmethod
    def description(cls) -> str | None:
        return "Match videos and overlays from two folders by file name."

    @classmethod
    def create_cli_command(cls) -> click.Command | None:
        """Return the folder-pair CLI command."""

        @click.command(name=cls.plugin_id(), help=cls.description() or None)
        @click.argument("videos_dir", type=click.Path(exists=True, path_type=Path, file_okay=False, dir_okay=True))
        @click.argument("overlays_dir", type=click.Path(exists=True, path_type=Path, file_okay=False, dir_okay=True))
        @click.option("--handler-type", required=True, help="File decoder handler type for matched overlay files.")
        @click.pass_context
        def command(ctx: click.Context, videos_dir: Path, overlays_dir: Path, handler_type: str) -> None:
            settings = {
                "videos_dir": str(videos_dir.resolve()),
                "overlays_dir": str(overlays_dir.resolve()),
                "handler_type": handler_type,
            }
            try:
                cls().resolve(settings)
            except (ValueError, OSError) as exc:
                raise click.ClickException(str(exc)) from exc
            runner = ctx.obj.get("run_with_items") if isinstance(ctx.obj, dict) else None
            if runner is None:
                raise click.ClickException("CLI runtime is not available.")
            runner([PlaylistItem(resolver=cls.plugin_id(), settings=settings)])

        return command

    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
        """Return the playlist of matched video and overlay files the settings describe."""
        return resolve_settings(settings)

    def create_settings_widget(self) -> PlaylistResolverWidget:
        from .settings_widget import FolderPairSettingsWidget

        return FolderPairSettingsWidget()


PLUGIN_CLASS = FolderPairResolverPlugin
