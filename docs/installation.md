# Installation

ax-devil supports **Linux** and requires **Python 3.10+** (CI checks 3.10 and 3.12).

## Ubuntu 24.04 prerequisites

Install the native dependencies for running the desktop app:

```bash
sudo apt-get update
sudo apt-get install -y git pipx python3-venv ffmpeg libegl1-mesa-dev libgl1-mesa-dev \
  libgles2-mesa-dev libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1
pipx install uv
pipx ensurepath
```

Open a new terminal if `uv` is not yet on your PATH. Other distributions need the equivalent Qt and FFmpeg
packages.

## Run from source

```bash
git clone https://github.com/RasmusRynell/ax-devil.git
cd ax-devil
uv sync --locked
uv run ax-devil
```

This opens an empty workspace. To run the [README examples](../README.md#open-your-data) from source, prefix them
with `uv run` in the repository directory.

## Install from PyPI

After the native prerequisites, install ax-devil as a standalone tool:

```bash
uv tool install ax-devil
ax-devil
```

`python -m pip install ax-devil` inside a virtual environment also works. Upgrade with `uv tool upgrade ax-devil`;
installed plugins follow on the next launch. See [plugin upgrades and recovery](plugins.md#upgrades-and-recovery).

## Graphics startup problems

Video and overlays are drawn on the GPU. Without a working GPU driver, Qt falls back to a mode that cannot draw
overlays and the log says so. On Linux, launch with `LIBGL_ALWAYS_SOFTWARE=1` to render with Mesa on the CPU; on
Windows, set `QSG_RHI_PREFER_SOFTWARE_RENDERER=1`.

## Developing

`uv sync --locked` includes the development tools. Install optional Git hooks with `uv run pre-commit install`
and fix formatting and lint issues with `make format`. See the [testing runbook](runbooks/testing.md) for checks,
isolated GUI verification, and installation tests.
GUI tests need `xvfb`, `xauth` and Mesa (`libgl1-mesa-dri`, or `xorg-server-xvfb` on Arch).
