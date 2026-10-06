"""MOT Challenge playlist resolver plugin.

Auto-discovers sequences from a MOT Challenge dataset directory and builds a
playlist with one entry per sequence.
"""

from __future__ import annotations

from pathlib import Path

import click

from ax_devil.modules.plugin_system import PlaylistResolverPlugin, PlaylistResolverWidget
from ax_devil.modules.workspace import PlaylistContent, ResolvedPlaylistStartup
from ax_devil.plugins.playlist_resolvers.mot_challenge.resolver import build_playlist_contents, discover_sequences


class MOTChallengeResolverPlugin(PlaylistResolverPlugin):
    """Resolve a playlist from a MOT Challenge dataset directory."""

    @classmethod
    def plugin_id(cls) -> str:
        return "mot_challenge"

    @classmethod
    def display_name(cls) -> str:
        return "MOT Challenge"

    @classmethod
    def description(cls) -> str | None:
        return "Load the train and test sequences of a MOT Challenge dataset folder."

    @classmethod
    def create_cli_command(cls) -> click.Command | None:
        """Return the MOT Challenge CLI command."""

        @click.command(name=cls.plugin_id(), help=cls.description() or None)
        @click.argument("dataset_dir", type=click.Path(exists=True, path_type=Path, file_okay=False, dir_okay=True))
        @click.pass_context
        def command(ctx: click.Context, dataset_dir: Path) -> None:
            playlists = cls._resolve_cli_dataset(dataset_dir)
            if not playlists:
                raise click.ClickException(f"No playlists were resolved from '{dataset_dir}'.")
            runner = ctx.obj.get("run_with_startup_content") if isinstance(ctx.obj, dict) else None
            if runner is None:
                raise click.ClickException("CLI runtime is not available.")
            runner(ResolvedPlaylistStartup(playlists=tuple(playlists)))

        return command

    @classmethod
    def _resolve_cli_dataset(cls, dataset_dir: Path) -> list[PlaylistContent]:
        """Resolve a MOT Challenge dataset root for CLI startup."""
        return build_playlist_contents(discover_sequences(dataset_dir))

    def create_settings_widget(self) -> PlaylistResolverWidget:
        from .settings_widget import MOTChallengeSettingsWidget

        return MOTChallengeSettingsWidget()


PLUGIN_CLASS = MOTChallengeResolverPlugin
