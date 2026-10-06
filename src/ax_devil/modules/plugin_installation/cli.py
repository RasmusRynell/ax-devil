"""User commands for isolated plugin installation."""

from __future__ import annotations

import subprocess

import click

from .host import current_host
from .project import change_plugins, installation_root, selected_plugins


class PluginCommands(click.Group):
    """Present installation failures as concise command-line errors."""

    def invoke(self, ctx: click.Context) -> object:
        """Keep recovery commands usable without loading the application."""
        try:
            return super().invoke(ctx)
        except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
            raise click.ClickException(f"{exc}") from exc


@click.group(cls=PluginCommands)
def plugins() -> None:
    """Install, update, list, or remove external plugins."""


@plugins.command("install")
@click.argument("specifications", nargs=-1, required=True)
@click.option("--index", "indexes", multiple=True, metavar="NAME=URL", help="Add a package index to this installation.")
def install_command(specifications: tuple[str, ...], indexes: tuple[str, ...]) -> None:
    """Install package requirements, wheels, or editable plugin directories."""
    change_plugins(current_host(), install=specifications, indexes=indexes)
    click.echo("Plugins installed. Restart ax-devil to use them.")


@plugins.command("update")
@click.option("--upgrade", is_flag=True, help="Also upgrade dependencies beyond the existing lockfile.")
@click.option("--index", "indexes", multiple=True, metavar="NAME=URL", help="Add a package index to this installation.")
def update_command(upgrade: bool, indexes: tuple[str, ...]) -> None:
    """Rebuild for the current app, preserving locked versions where compatible."""
    change_plugins(current_host(), upgrade=upgrade, indexes=indexes)
    click.echo("Plugins updated. Restart ax-devil to use them.")


@plugins.command("remove")
@click.argument("names", nargs=-1)
@click.option(
    "--all", "clear", is_flag=True, help="Remove the complete plugin installation, including old environments."
)
def remove_command(names: tuple[str, ...], clear: bool) -> None:
    """Remove plugin distributions by name."""
    if bool(names) == clear:
        raise click.UsageError("Specify plugin names or --all")
    change_plugins(current_host(), remove=names, clear=clear)
    click.echo("Plugins removed.")


@plugins.command("list")
def list_command() -> None:
    """Show selected plugin distributions and their package requirements or sources."""
    selected = selected_plugins(installation_root(current_host()) / "current")
    for name, path in selected.items():
        click.echo(f"{name}: {path}")
    if not selected:
        click.echo("No external plugins installed.")
