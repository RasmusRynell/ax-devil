"""Shared application CLI options, usable before importing Qt."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click

from ax_devil.modules.settings.paths import DEFAULT_CONFIG_PATH

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


CONFIG_OPTION = click.option(
    "--config",
    type=click.Path(path_type=Path, dir_okay=False),
    help=f"Path to configuration file (default: {DEFAULT_CONFIG_PATH})",
)

LOG_LEVEL_OPTION = click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(LOG_LEVELS),
    help="Set logging level (default: INFO)",
)
DEBUG_OPTION = click.option("--debug", is_flag=True, help="Enable debug window")

F = TypeVar("F", bound=Callable[..., Any])


def apply_run_options(command: F) -> F:
    """Apply standard --log-level, --config, --debug options."""
    return LOG_LEVEL_OPTION(CONFIG_OPTION(DEBUG_OPTION(command)))
