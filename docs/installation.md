# Installation

ax-devil supports **Linux**. The language version and tooling are in the [toolchain](toolchain.md).

## Ubuntu 24.04 prerequisites

Install the native dependencies for running the desktop app:

```bash
sudo apt-get update
sudo apt-get install -y git pipx python3-venv ffmpeg libegl1-mesa-dev libgl1-mesa-dev \
  libgles2-mesa-dev libgl1-mesa-dri libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-render-util0 libxcb-xkb1 libxkbcommon-x11-0
pipx install uv
pipx ensurepath
```

Open a new terminal if `uv` is not yet on your PATH. Other distributions need the equivalent Qt and FFmpeg
packages.

## Run from source

```bash
git clone https://github.com/RasmusRynell/ax-devil.git
cd ax-devil
```

Then build and launch with the [toolchain](toolchain.md#install-and-launch) commands.

The launch opens an empty workspace. To run the [README examples](../README.md#open-your-data) from source, use the
same launch command in the repository directory.

## Install from PyPI

After the native prerequisites, install ax-devil as a standalone tool:

```bash
uv tool install ax-devil
ax-devil
```

Upgrade with `uv tool upgrade ax-devil`;
installed plugins follow on the next launch. See [plugin upgrades and recovery](plugins.md#upgrades-and-recovery).

## Graphics startup problems

Video and overlays are drawn on the GPU. Without a working GPU driver, Qt falls back to a mode that cannot draw
overlays and the log says so. On Linux, launch with `LIBGL_ALWAYS_SOFTWARE=1` to render with Mesa on the CPU; this
needs the Mesa DRI driver (`libgl1-mesa-dri`, installed by the prerequisites above). On Windows, set
`QSG_RHI_PREFER_SOFTWARE_RENDERER=1`.

## Developing

The [toolchain](toolchain.md) lists the development setup and commands. Fix formatting and lint issues with
`make format`. See the [testing runbook](runbooks/testing.md) for checks, isolated GUI verification, and installation
tests.
