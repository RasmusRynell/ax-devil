"""``ax-devil catalog``: list, check, render and create render catalogs from the command line.

These commands let an AI agent that edits a catalog file check its work without the app: ``check`` loads the file
and draws every example sheet, and ``render`` writes the sheets the catalog viewer shows as PNG images to look at.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import click

from ax_devil.cli_options import CONFIG_OPTION
from ax_devil.modules.scene.rendering import SceneRenderCatalog, SceneRenderCatalogLoader, SceneRenderCatalogStore
from ax_devil.modules.settings.config_manager import ConfigManager

_APPLICATION: list[object] = []
"""Keeps the off-screen Qt application of a command alive until the process ends."""
PATH_ARGUMENT = click.argument("path", required=False, type=click.Path(path_type=Path, dir_okay=False))


@click.group("catalog", invoke_without_command=True)
@click.pass_context
def catalog(ctx: click.Context) -> None:
    """Render catalogs: how overlays are drawn.

    Without a command, start the app with the catalog viewer open. The commands list catalogs, check one, render its
    examples, start a new one or choose the default.
    """
    if ctx.invoked_subcommand is None:
        ctx.obj["run_catalog_viewer"]()


@catalog.command("list")
@CONFIG_OPTION
def list_catalogs(config: Path | None) -> None:
    """List every catalog with its file; * marks the default for new views."""
    listing = _store(config).list_catalogs_with_errors()
    for listed in listing.catalogs:
        mark = "*" if listed.path == listing.default_path else " "
        note = "  (built-in, read-only)" if listed.is_built_in else ""
        click.echo(f"{mark} {listed.name:<28} {listed.path}{note}")
    for error in listing.errors:
        click.echo(f"! {error.path.name:<28} {error.path}  (does not load: {error.message})")


@catalog.command()
@PATH_ARGUMENT
@CONFIG_OPTION
def check(path: Path | None, config: Path | None) -> None:
    """Load PATH (default: the chosen default catalog) and draw every example sheet; fail on the first problem.

    Errors name their place in the file, such as $.templates.object_label.parameters.id_font_size.
    """
    from ax_devil.modules.catalog_viewer.sheets import drawing_errors, sheets

    catalog_path, loaded, document = _load(path, config)
    _application()
    problems = [
        f"{sheet.title}: {diagnostic.location}: {diagnostic.message}"
        for sheet in sheets(document)
        for diagnostic in drawing_errors(sheet, loaded)
    ]
    if problems:
        listed = "\n".join(problems)
        raise click.ClickException(f"Drawing the examples failed:\n{listed}")
    recipes = document.get("recipes", {})
    objects = len(recipes.get("classifications", [])) + len(recipes.get("fallbacks", []))
    relations = len(recipes.get("relations", []))
    components = len(document.get("templates", {}))
    counts = ", ".join(
        f"{count} {noun}{'' if count == 1 else 's'}"
        for count, noun in ((objects, "object type"), (relations, "relation"), (components, "component"))
    )
    click.echo(f"✓ {catalog_path}: {counts}")


@catalog.command()
@PATH_ARGUMENT
@click.option(
    "--out",
    "out_dir",
    required=True,
    type=click.Path(path_type=Path, file_okay=False),
    help="Directory to write one PNG per sheet into",
)
@click.option(
    "--sheet", "sheet_key", help="Render only this sheet: overview, street, crowd, or an object type or relation id"
)
@CONFIG_OPTION
def render(path: Path | None, out_dir: Path, sheet_key: str | None, config: Path | None) -> None:
    """Draw the example sheets of PATH (default: the default catalog) as PNG images, as the viewer shows them."""
    from shiboken6 import delete

    from ax_devil.modules.catalog_viewer.sheets import drawing_errors, sheet_frame, sheets
    from ax_devil.modules.video_player.engine.quick.image_renderer import FrameImageRenderer

    _catalog_path, loaded, document = _load(path, config)
    _application()
    chosen = [sheet for sheet in sheets(document) if sheet_key in (None, sheet.key)]
    if not chosen:
        keys = ", ".join(sheet.key for sheet in sheets(document))
        raise click.ClickException(f"No sheet named {sheet_key}. Sheets: {keys}")
    out_dir.mkdir(parents=True, exist_ok=True)
    renderer = FrameImageRenderer()
    try:
        for sheet in chosen:
            target = out_dir / f"{sheet.key}.png"
            if not renderer.render_frame(sheet_frame(sheet, loaded)).save(str(target)):
                raise click.ClickException(f"Could not write PNG image: {target}")
            click.echo(f"{target}  {sheet.description}")
            for diagnostic in drawing_errors(sheet, loaded):
                click.echo(f"  drawing error: {diagnostic.location}: {diagnostic.message}")
    finally:
        renderer.cleanup()
        # Destroy native nodes while their Python texture adapters are still alive.
        # This command does not run an event loop to service deleteLater().
        delete(renderer)


@catalog.command()
@click.argument("path", type=click.Path(path_type=Path, dir_okay=False, exists=True))
@CONFIG_OPTION
def use(path: Path, config: Path | None) -> None:
    """Make PATH the default catalog: new views and the catalog viewer switch to it."""
    catalog_path, _loaded, _document = _load(path.absolute(), config)
    try:
        _store(config).set_default_catalog(catalog_path)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Default catalog: {catalog_path}")


@catalog.command()
def reference() -> None:
    """Print the catalog language: types, operations, primitives, recipe inputs, bindings and limits."""
    from ax_devil.modules.catalog_viewer.reference import language_reference

    click.echo(language_reference())


@catalog.command()
@click.argument("name")
@click.option(
    "--from",
    "base",
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
    help="Catalog to copy (default: the default built-in catalog, Standard)",
)
@CONFIG_OPTION
def new(name: str, base: Path | None, config: Path | None) -> None:
    """Create an editable catalog called NAME as a copy of another, and print its file."""
    try:
        created = _store(config).create_catalog(name, base_catalog_path=base)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(created.path)


def _store(config: Path | None) -> SceneRenderCatalogStore:
    """Return the catalog store of the app's configuration, or of *config*."""
    if config is not None:
        if not config.exists():
            raise click.ClickException(f"Config file does not exist: {config}")
        ConfigManager().set_config_path(config, create_if_missing=False)
    return SceneRenderCatalogStore()


def _load(path: Path | None, config: Path | None) -> tuple[Path, SceneRenderCatalog, Mapping[str, Any]]:
    """Return the catalog at *path*, or the default catalog, compiled and as a document; fail with its error."""
    # The chosen default, even a broken one: checking the built-in instead would hide the error.
    catalog_path = path or _store(config).list_catalogs_with_errors().chosen_default_path
    try:
        revision = SceneRenderCatalogLoader().load_revision(catalog_path)
    except (OSError, ValueError) as error:
        raise click.ClickException(f"{catalog_path} does not load:\n{error}") from error
    return catalog_path, revision.catalog, revision.document


def _application() -> None:
    """Start an off-screen Qt application, which text layout and image rendering need."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:
        _APPLICATION.append(QApplication([]))
