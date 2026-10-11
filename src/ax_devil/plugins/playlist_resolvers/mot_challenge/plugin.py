"""MOT Challenge playlist resolver plugin.

Auto-discovers sequences from a MOT Challenge dataset directory and builds a
playlist with one entry per sequence.
"""

from __future__ import annotations

from pathlib import Path

import click

from ax_devil.modules.plugin_system import PlaylistResolverPlugin, PlaylistResolverWidget
from ax_devil.modules.workspace.core import PlaylistContent, PlaylistItem, PlaylistSettings
from ax_devil.plugins.playlist_resolvers.mot_challenge.resolver import resolve_settings


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
        """Return the MOT Challenge CLI command, which opens every sequence of the dataset."""

        @click.command(name=cls.plugin_id(), help=cls.description() or None)
        @click.argument("dataset_dir", type=click.Path(exists=True, path_type=Path, file_okay=False, dir_okay=True))
        @click.pass_context
        def command(ctx: click.Context, dataset_dir: Path) -> None:
            settings = {"root": str(dataset_dir.resolve())}
            runner = ctx.obj.get("run_with_items") if isinstance(ctx.obj, dict) else None
            if runner is None:
                raise click.ClickException("CLI runtime is not available.")
            runner([PlaylistItem(resolver=cls.plugin_id(), settings=settings)])

        return command

    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
        """Return the playlist of the MOT sequences the settings describe."""
        return resolve_settings(settings)

    def create_settings_widget(self) -> PlaylistResolverWidget:
        from .settings_widget import MOTChallengeSettingsWidget

        return MOTChallengeSettingsWidget()


PLUGIN_CLASS = MOTChallengeResolverPlugin
