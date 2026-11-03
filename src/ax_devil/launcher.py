"""Choose the application interpreter before importing Qt or application code."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from ax_devil.cli_options import apply_run_options
from ax_devil.modules.plugin_installation.host import current_host
from ax_devil.modules.plugin_installation.project import (
    OutdatedPluginEnvironment,
    installation_root,
    refresh_plugins,
    runtime_python,
)

# What the launcher hands the plugin interpreter, which cannot work either out for itself.
_LAUNCH_COMMAND = "AX_DEVIL_LAUNCH_COMMAND"
_PLUGIN_ROOT = "AX_DEVIL_PLUGIN_ROOT"


def launch_command() -> list[str]:
    """Return the command that starts this app through its launcher, without arguments.

    Restarting and managing plugins from the running app go through it, so they run in the base app and a restart
    picks up the current plugin selection, not the plugin environment this process runs in.
    """
    recorded = os.environ.get(_LAUNCH_COMMAND)
    return [str(part) for part in json.loads(recorded)] if recorded else _own_command()


def plugin_root() -> Path:
    """Return where this app installation keeps its plugin selection.

    Raise ``OSError`` or ``ValueError`` when the app installation cannot be identified.
    """
    recorded = os.environ.get(_PLUGIN_ROOT)
    return Path(recorded) if recorded else installation_root(current_host())


def _own_command() -> list[str]:
    """Return the interpreter and script or module that started this process, without the user's arguments."""
    return sys.orig_argv[: len(sys.orig_argv) - (len(sys.argv) - 1)]


def main() -> None:
    """Launch the configured application or manage plugins from the base environment."""
    arguments = sys.argv[1:]
    parser = apply_run_options(click.Command("ax-devil", add_help_option=False))
    try:
        with parser.make_context(
            "ax-devil", list(arguments), allow_extra_args=True, allow_interspersed_args=False
        ) as context:
            remaining = context.args
    except click.ClickException:
        # Let the application CLI render help or errors; never run management after a parse failure.
        remaining = []
    if remaining[:1] == ["plugins"]:
        from ax_devil.modules.plugin_installation.cli import plugins

        plugins(args=remaining[1:], prog_name="ax-devil plugins")
        return
    command = "ax-devil"
    try:
        host = current_host()
        command = host.command
        try:
            interpreter = runtime_python(host)
        except OutdatedPluginEnvironment:
            # Rebuild the same plugin selection for the upgraded app before the first launch uses it.
            sys.stderr.write("ax-devil or its dependencies changed; updating plugins.\n")
            refresh_plugins(host)
            interpreter = runtime_python(host)
        if interpreter is not None:
            environment = {
                **os.environ,
                _LAUNCH_COMMAND: json.dumps(_own_command()),
                _PLUGIN_ROOT: str(installation_root(host)),
            }
            os.execve(str(interpreter), [str(interpreter), "-I", "-m", "ax_devil.cli", *arguments], environment)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.stderr.write(
            f"Plugin installation unavailable: {exc}. Starting the base application.\n"
            f"Repair with: {command} plugins update\n"
        )
    from ax_devil.cli import cli

    cli(prog_name="ax-devil")


if __name__ == "__main__":
    main()
