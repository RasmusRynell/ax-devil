"""Folder-pair playlist resolver plugin."""

from __future__ import annotations

from pathlib import Path

import click

from ax_devil.modules.plugin_system import PlaylistResolverPlugin, PlaylistResolverWidget
from ax_devil.modules.workspace.core import PlaylistContent, ResolvedPlaylistStartup
from ax_devil.plugins.playlist_resolvers.folder_pair.resolver import build_playlist_contents, discover_folder_pairs


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
            playlists = cls._resolve_cli_dataset(videos_dir, overlays_dir, handler_type)
            if not playlists:
                raise click.ClickException(
                    f"No matched video/overlay pairs were resolved from '{videos_dir}' and '{overlays_dir}'."
                )
            runner = ctx.obj.get("run_with_startup_content") if isinstance(ctx.obj, dict) else None
            if runner is None:
                raise click.ClickException("CLI runtime is not available.")
            runner(ResolvedPlaylistStartup(playlists=tuple(playlists)))

        return command

    @classmethod
    def _resolve_cli_dataset(cls, videos_dir: Path, overlays_dir: Path, handler_type: str) -> list[PlaylistContent]:
        """Resolve folder-pair inputs for CLI startup."""
        return build_playlist_contents(discover_folder_pairs(videos_dir, overlays_dir), handler_type)

    def create_settings_widget(self) -> PlaylistResolverWidget:
        from .settings_widget import FolderPairSettingsWidget

        return FolderPairSettingsWidget()


PLUGIN_CLASS = FolderPairResolverPlugin
