"""Start a private Xvfb display so GUI checks never touch the user's desktop (stdlib only).

Displays are probed upward from ``first`` and only ones with no X11 socket or lock file are used, so this never
connects to, or disturbs, another X server. Rendering needs a real GL context: under Mesa the display gives
OpenGL on llvmpipe, which is what the tests and the benchmarks use.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

_SOCKET_FOLDER = Path("/tmp/.X11-unix")
_LAST_PROBED = 40  # Displays tried from ``first``.
_STARTUP_SECONDS = 10.0
# Settings that make Qt pick another platform or graphics backend than the display and Mesa asked for.
_UNSET = (
    "WAYLAND_DISPLAY",
    "QT_QUICK_BACKEND",
    "QSG_RHI_BACKEND",
    "QT_WIDGETS_RHI",
    "QT_WIDGETS_RHI_BACKEND",
    "QT_QPA_PLATFORMTHEME",
)


class PrivateDisplay(NamedTuple):
    """A running Xvfb: its display name (such as ``:90``) and process."""

    name: str
    process: subprocess.Popen[bytes]


def _free(number: int) -> bool:
    """Return whether no X server socket or lock file exists for display *number*."""
    return not (_SOCKET_FOLDER / f"X{number}").exists() and not Path(f"/tmp/.X{number}-lock").exists()


def _owned(number: int, process: subprocess.Popen[bytes]) -> bool:
    """Return whether display *number* is served by *process*; a lock file naming another pid means it lost a race."""
    try:
        return int(Path(f"/tmp/.X{number}-lock").read_text().strip()) == process.pid
    except (OSError, ValueError):
        return False


def start(width: int = 1920, height: int = 1080, first: int = 90) -> PrivateDisplay:
    """Start Xvfb on the first free display from ``:first`` upward and return it once it accepts connections."""
    executable = shutil.which("Xvfb")
    if executable is None:
        raise RuntimeError(
            "Xvfb is required for GUI tests and rendering checks and was not found "
            "(Debian/Ubuntu: sudo apt install xvfb xauth libgl1-mesa-dri; "
            "Arch: sudo pacman -S xorg-server-xvfb xorg-xauth)"
        )
    for number in range(first, first + _LAST_PROBED):
        if not _free(number):
            continue
        name = f":{number}"
        process = subprocess.Popen(
            [executable, name, "-screen", "0", f"{width}x{height}x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + _STARTUP_SECONDS
        while time.monotonic() < deadline and process.poll() is None:
            if (_SOCKET_FOLDER / f"X{number}").exists() and _owned(number, process):
                return PrivateDisplay(name, process)
            time.sleep(0.02)
        stop(PrivateDisplay(name, process))  # Lost a race for this number, or never came up: try the next one.
    raise RuntimeError(f"Could not start Xvfb on a free display from :{first}")


def stop(display: PrivateDisplay) -> None:
    """Stop the Xvfb process of *display* and wait for it."""
    process = display.process
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def environment(name: str, *, mesa: bool = True) -> dict[str, str | None]:
    """Return the environment for Qt on display *name*; a value of None means the variable must be unset.

    With ``mesa`` the graphics stack is Mesa's software OpenGL (llvmpipe), overriding a desktop GPU driver choice.
    """
    values: dict[str, str | None] = dict.fromkeys(_UNSET)
    values["QT_QPA_PLATFORM"] = "xcb"
    values["DISPLAY"] = name
    if mesa:
        values["LIBGL_ALWAYS_SOFTWARE"] = "1"
        values["__GLX_VENDOR_LIBRARY_NAME"] = "mesa"
    return values


@contextlib.contextmanager
def private_display(width: int = 1920, height: int = 1080, *, mesa: bool = True) -> Iterator[dict[str, str | None]]:
    """Run a private Xvfb for the ``with`` block and yield its :func:`environment`."""
    display = start(width, height)
    try:
        yield environment(display.name, mesa=mesa)
    finally:
        stop(display)
