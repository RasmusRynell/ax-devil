"""Record reproducible ax-devil demos without using the desktop."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from types import FrameType
from typing import NoReturn, TextIO


def run_capture(command: list[str], env: dict[str, str], log: TextIO) -> int:
    """Own one process group, including Xvfb, and reap it on success, failure or interruption."""
    process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        return process.wait()
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        # The wrapper may have exited while one of its descendants ignored SIGTERM.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _terminated(signum: int, _frame: FrameType | None) -> NoReturn:
    raise SystemExit(128 + signum)


def isolated_environment(home: Path) -> dict[str, str]:
    """Give the recording process its own storage and software OpenGL display settings."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AX_DEVIL_", "QT_", "QSG_", "XDG_"))
        and key not in {"DISPLAY", "WAYLAND_DISPLAY", "PYTHONPATH"}
    }
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_DATA_HOME=str(home / "data"),
        XDG_CACHE_HOME=str(home / "cache"),
        XDG_STATE_HOME=str(home / "state"),
        XDG_RUNTIME_DIR=str(home / "runtime"),
        TMPDIR=str(home / "tmp"),
        QT_QPA_PLATFORM="xcb",
        QT_SCALE_FACTOR="1",
        QT_WIDGETS_RHI="1",
        __GLX_VENDOR_LIBRARY_NAME="mesa",
        LIBGL_ALWAYS_SOFTWARE="1",
    )
    (home / "runtime").mkdir(mode=0o700)
    (home / "tmp").mkdir(mode=0o700)
    return env


def positive_int(value: str) -> int:
    """Parse a positive count."""
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def parser() -> argparse.ArgumentParser:
    """Build the capture and render command interface."""
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="Record an offline video in the real application on private Xvfb")
    capture.add_argument("--video", type=Path, required=True)
    capture.add_argument("--overlay", type=Path)
    capture.add_argument("--handler", help="Built-in decoder name, e.g. UVG_VCM")
    capture.add_argument("--compare-overlay", type=Path, help="Second overlay using the same handler")
    capture.add_argument("--simulate-det", action="store_true", help="Generate an imperfect UVG_VCM lane from GT")
    capture.add_argument("--recipe", choices=("playback", "catalogs", "compare"), default="playback")
    capture.add_argument(
        "--catalogs", nargs="+", default=["glass", "standard", "tracking", "chunky", "classic", "minimal"]
    )
    capture.add_argument("--frames", type=positive_int, default=300, help="Output frames (default: 300)")
    capture.add_argument("--fps", type=positive_int, default=30)
    capture.add_argument("--start", type=float, default=0, help="Source start time in seconds")
    capture.add_argument("--width", type=positive_int, default=1600)
    capture.add_argument("--height", type=positive_int, default=900)
    capture.add_argument(
        "--inspector-at", type=float, default=1, help="Open media tools at this output second; -1 disables"
    )
    capture.add_argument("--raw-only", action="store_true")
    capture.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    render = commands.add_parser("render", help="Restyle an existing raw capture without launching the app")
    render.add_argument("--input", type=Path, required=True)
    for command in (capture, render):
        command.add_argument(
            "--output", type=Path, required=True, help="New output directory; existing paths are refused"
        )
        command.add_argument("--style", choices=("plain", "midnight"), default="midnight")
    return root


def main() -> int:
    """Validate inputs, isolate capture, and optionally style the result."""
    cli = parser()
    args = cli.parse_args()
    if args.command == "capture":
        from math import isfinite

        if args.width % 2 or args.height % 2:
            cli.error("width and height must be even")
        if not isfinite(args.start) or args.start < 0 or not isfinite(args.inspector_at):
            cli.error("start must be finite and nonnegative; inspector-at must be finite")
        for key in ("video", "overlay", "compare_overlay"):
            path = getattr(args, key)
            if path is not None:
                if not path.is_file():
                    cli.error(f"file does not exist: {path}")
                setattr(args, key, path.resolve())
        if bool(args.overlay) != bool(args.handler):
            cli.error("--overlay and --handler must be supplied together")
        if args.recipe == "catalogs" and args.overlay is None:
            cli.error("catalogs requires --overlay and --handler")
        if args.recipe == "compare":
            if args.overlay is None or bool(args.compare_overlay) == args.simulate_det:
                cli.error("compare requires an overlay and exactly one of --compare-overlay / --simulate-det")
        elif args.compare_overlay or args.simulate_det:
            cli.error("comparison options require --recipe compare")
        if args.simulate_det and args.handler != "UVG_VCM":
            cli.error("--simulate-det requires --handler UVG_VCM")
        if args.worker:
            if os.environ.get("AX_DEVIL_DEMO_WORKER") != "1" or not os.environ.get("DISPLAY"):
                cli.error("worker must be launched by the recorder")
            from .capture import capture

            capture(args)
            return 0
        for tool in ("xvfb-run", "Xvfb", "xauth"):
            if shutil.which(tool) is None:
                cli.error(f"missing {tool}; see tools/demo_recorder/README.md")
    elif not args.input.is_file():
        cli.error(f"file does not exist: {args.input}")
    if (args.command == "render" or not args.raw_only) and shutil.which("ffmpeg") is None:
        cli.error("ffmpeg is required for WebP export")
    args.output = args.output.resolve()
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        cli.error(f"output already exists; choose a new directory: {args.output}")
    if args.command == "capture":
        with tempfile.TemporaryDirectory(prefix="ax-devil-demo-") as temporary:
            env = isolated_environment(Path(temporary))
            env["AX_DEVIL_DEMO_WORKER"] = "1"
            # Absolute input/output arguments survive a different invoking working directory.
            argv = sys.argv[1:]
            for key in ("video", "overlay", "compare_overlay", "output"):
                option = f"--{key.replace('_', '-')}"
                if option in argv:
                    argv[argv.index(option) + 1] = str(getattr(args, key))
            with (args.output / "capture.log").open("w") as log:
                returncode = run_capture(
                    [
                        "xvfb-run",
                        "-a",
                        "-e",
                        str(args.output / "xvfb.log"),
                        "-s",
                        f"-screen 0 {args.width}x{args.height}x24 -nolisten tcp",
                        sys.executable,
                        "-m",
                        "tools.demo_recorder",
                        *argv,
                        "--worker",
                    ],
                    env,
                    log,
                )
            if returncode:
                sys.stderr.write(f"Capture failed; see {args.output / 'capture.log'}\n")
                return returncode
        if args.raw_only:
            return 0
        args.input = args.output / "raw.mp4"
    # Render never needs a native display.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.pop("QT_QPA_PLATFORMTHEME", None)
    from .render import render

    render(args.input, args.output, args.style)
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _terminated)
    raise SystemExit(main())
