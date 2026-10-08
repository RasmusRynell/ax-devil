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

If graphics initialization fails, launch with software rendering:

```bash
QT_WIDGETS_RHI=0 QT_QUICK_BACKEND=software ax-devil         # standalone installation
QT_WIDGETS_RHI=0 QT_QUICK_BACKEND=software uv run ax-devil  # source checkout
```

Then set **Settings → General → Appearance → Graphics acceleration** to **Off** to keep software rendering
on later launches. See [graphics acceleration](settings.md#graphics-acceleration).

## Developing

`uv sync --locked` includes the development tools. Install optional Git hooks with `uv run pre-commit install`
and fix formatting and lint issues with `make format`. See the [testing runbook](runbooks/testing.md) for checks,
isolated GUI verification, and installation tests.
