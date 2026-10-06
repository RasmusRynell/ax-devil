"""Choose the application interpreter before importing Qt or application code."""

from __future__ import annotations

import os
import subprocess
import sys

import click

from ax_devil.cli_options import apply_run_options
from ax_devil.modules.plugin_installation.host import current_host
from ax_devil.modules.plugin_installation.project import OutdatedPluginEnvironment, refresh_plugins, runtime_python


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
            os.execv(str(interpreter), [str(interpreter), "-I", "-m", "ax_devil.cli", *arguments])
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        sys.stderr.write(
            f"Plugin installation unavailable: {exc}. Starting the base application.\n"
            f"Repair with: {command} plugins update\n"
        )
    from ax_devil.cli import cli

    cli(prog_name="ax-devil")


if __name__ == "__main__":
    main()
