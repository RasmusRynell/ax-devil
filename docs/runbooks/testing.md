# Testing

## Running tests

```bash
make test                                       # Regular offline suite, on a private Xvfb display
make test-integration                           # Real development, pip/tool, and upgrade checks; may download packages
make check                                     # Formatting, linting, logging conventions, and strict types
```

`make test` distributes whole files across available CPUs, capped at eight workers. Use
`make test TEST_WORKERS=0` for serial debugging or set a smaller worker count on constrained machines.
Each worker owns a separate temporary home and app storage; subprocesses inside a worker reuse that worker's home.
Focused `uv run pytest ...` commands and the installation integration suite stay serial.

To run a specific test file or test:

```bash
uv run pytest tests/modules/workspace/core/test_content.py
uv run pytest tests/modules/workspace/core/test_content.py::test_specific_case -v
uv run pytest tests/ -k "keyword"
```

Plugin runtime checks:

```bash
uv run pytest tests/modules/plugin_system tests/modules/plugin_installation
uv run ax-devil plugins --help
uv run ax-devil --help
```

The regular suite covers plugin installation with uv mocked, plus real entry-point discovery and decoding of the
minimal package from `.agents/skills/write-plugin/reference.md` in fresh Python processes.

`make test-integration` installs the app and that plugin for real (editable, `uv tool install`, and pip from wheels)
outside the checkout with isolated storage, then checks launching, decoding, upgrades, rollback, refresh at launch and
removal. It may download and build packages, needs the same system libraries as a normal installation, and publishes
nothing.

CI runs these jobs on pull requests, `main`, and release tags: `quality` shares one Python 3.12 environment for
application checks, the regular suite, installation smoke tests, and package builds; `test` runs
the regular suite on the minimum supported Python 3.10 on Ubuntu, Windows (Direct3D 11 on the WARP software adapter)
and macOS, serially because windows on one native desktop fight over focus; `lowest` runs it on Python 3.10 with the oldest allowed
dependencies (`uv pip install --resolution lowest-direct -e . --group dev`). A newer push to a PR cancels its
superseded run; runs for `main` commits and releases always finish.
Pytest excludes the `integration` marker by default; `-m integration` selects it explicitly.

## Package artifacts

`uv build` builds the wheel and source distribution. CI runs it on pull requests, main pushes, and version tags.
Artifacts are uploaded only for release tags. On a `v*` tag, after all three CI jobs pass, CI runs
`tools/release-check/run.sh` and publishes the built artifacts to PyPI through trusted publishing.
A published release must remain available to the plugin resolver at its exact version.

`tools/release-check/run.sh [tool|pip|py310|source]` needs Docker. It installs the candidate in fresh Ubuntu 24.04
containers by running the documented prerequisite and source commands as written. The `tool`, `pip`, and `py310`
scenarios install the built wheel, with a local directory standing in for PyPI and every dependency coming from PyPI;
they render catalogs, install the documented plugin, play a generated video on a virtual display, upgrade the app, and check that
plugins follow. `source` clones the candidate, runs `make check`, `make test`, and `make test-integration`, and checks
that editable plugin edits apply without reinstalling.

## GUI verification

All agent verification runs away from the user's desktop, on a private Xvfb display with Mesa's OpenGL (llvmpipe).
The test suite starts one itself (see below), so plain `make test` and `uv run pytest` need nothing else; Xvfb must be
installed: `sudo pacman -S --needed xorg-server-xvfb xorg-xauth` on Arch/Omarchy, or
`sudo apt install xvfb xauth libgl1-mesa-dri` on Debian/Ubuntu.
Save `QWidget.grab()` or `QQuickWindow.grabWindow()` output to `tmp_path` or `/tmp` and inspect that image. Send
interactions through Qt test events. For application UI, see
[the write-ui skill's checks](../../.agents/skills/write-ui/SKILL.md#check-it).

Standalone GUI tools and commands that render (`ax-devil catalog render`, the app itself) get a display from
`xvfb-run -a -s "-screen 0 1920x1080x24"`, or from `tools/private_display.py`, which starts one on a free display
number without touching others. They must close their windows and workers when finished.
The pytest suite isolates app storage too; standalone app checks must use temporary settings and fixtures.

Xvfb has no window manager, so tests that need keyboard focus activate their window first. It does not verify the
physical GPU or its driver, Wayland, or window-manager interaction; those need a separate session or machine with its
own display and input. Never launch verification windows, send input, or capture screenshots on the user's desktop.
If the required isolated environment is unavailable, report the check as unrun and its coverage limit.
Real-device checks use configured test devices; the offline suite mocks external services.

## Test organization

```
tests/
├── conftest.py                    Shared fixtures (temp dirs, plugin loading, Qt display)
├── core/                          Root CLI, package metadata, and `ax_devil.core` tests
├── modules/                       Concept-owned module tests for workspace, viewer workflow, scene, sources, sync, cache, and playback
├── plugins/                       Plugin decoder and playlist resolver tests
└── helpers/                       Test utility modules
```

Config and settings tests live under `tests/modules/settings/`.

## Key conventions

- **Regular tests stay offline.** Only the explicit installation integration test may download packages. Mock all external services (RTSP, MQTT, device APIs).
- **Private display.** `tests/__init__.py` starts Xvfb (one per xdist worker, so windows cannot take each other's
  keyboard focus) and sets `QT_QPA_PLATFORM=xcb`, Mesa's software OpenGL and `DISPLAY` before importing Qt or
  application modules, replacing inherited desktop settings, so plain `uv run pytest`, CI and a stray
  `QT_QPA_PLATFORM=offscreen` all use the same platform; Qt Quick has no GPU backend under offscreen. Missing Xvfb
  fails the run. Off Linux the suite uses Qt's native platform plugin and starts no display; the graphics API comes
  from the `QSG_RHI_*` variables (see CI). `AX_DEVIL_TESTS_QPA_PLATFORM` names a platform plugin on any OS and skips Xvfb. Create
  applications through pytest-qt's `qapp`/`qtbot`, never during collection.
- **Plugins isolated.** A session-scoped fixture loads built-ins while installed external entry points are patched out.
- **Qt teardown.** Register widgets with `qtbot.addWidget()`. Register only top-level widgets: Qt deletes children with their parent, and a registered child whose parent was garbage-collected first makes pytest-qt close an already-deleted widget. The shared teardown hook drains Qt's deferred deletions after pytest-qt closes widgets, keeping native destruction on the GUI thread and out of later tests. Like the app, the suite runs with automatic cyclic GC off and collects in that hook, so a collection never runs inside a Qt callback; do not re-enable `gc` in a test without restoring it.
- **Temp directories.** Use pytest's `tmp_path` for file-system tests. `temp_dir` is an alias; `test_temp_root` supplies a session directory for shared generated media. Pytest manages their retention and cleanup.
- **Catalog fixtures.** Shared UI fixtures give each test its own empty user catalog store. For management, selection and validation tests, use `small_catalog_path` as `create_catalog(..., base_catalog_path=small_catalog_path)` or get fresh document data from `tests.catalog_helpers.catalog_document()`. The source bytes are immutable; each user copy still passes real schema validation and compilation. Keep packaged-catalog checks for its schema, sheets and rendered output. Tests must never write to the built-in catalog path. `test_scene_render_catalog_state_model.py` checks selections and selectors after every step of named scenarios and seeded random sequences; add a scenario there for a state bug that focused tests do not cover.
- **Prefer controller-level tests** over widget-heavy tests. `pytest-qt` is available but keep widget interactions minimal.
- **Isolated and fast.** Each test should be independent. No shared mutable state between tests.

## Writing a new test

Delete the tests of code you remove or merge first; test each rule once, at the layer that owns it. A new test
should protect a meaningful failure: wrong frames or overlays, lost settings, leaked workers, failed plugin loading,
or damaged output. Prefer strengthening an existing case over adding another smoke test.
Assert observable output using explicit expectations; avoid constructor echoes, deleted-name checks, route-equivalence
checks, private state and exact internal call sequences. Fake external transports or processes, while keeping owned
components real where practical.
Use events/signals for worker completion, not arbitrary sleeps or elapsed-time thresholds as proof of correctness.
Keep generated media tiny and reuse it when independent tests can safely share an immutable fixture.
`video_file_factory(duration, fps)` reuses generated video files; each caller must own and close its decoder,
worker and frame cache. Treat those source files as immutable; copy them to `tmp_path` before source-invalidation tests
modify them. Immutable reference images and TLS certificates can use module/session fixtures, while readers, servers,
and writable caches remain independent. A cache or prefetch test should check retained frames and require successful delivery
without additional decoding; request speed alone cannot prove a cache hit.

## Tests never touch the user's files

`tests/__init__.py` gives every run its own temporary home folder (`HOME` and the XDG folders) before any app code is
imported, so configuration, render catalogs, window state, logs, caches and plugins all land there; the folder is
removed when the owning process ends, and nested runs reuse it without deleting their parent's home.
The session hooks in `tests/conftest.py` record the user's real
`~/.ax_devil/configs` and `render_catalogs` and the plugin data folder before the run, and fail it if any of
them changed. After each test the shared configuration is pointed back at the suite's own file, so a test that switches
it cannot affect later ones. A test that needs files uses `tmp_path`.

## Quick renderer verification

`tests/modules/video_player/engine/test_quick_*.py` exercise rendered output, text/path
ordering, opacity, texture replacement, software rendering, and reparenting.
`tests/modules/video_player/engine/test_image_renderer.py` verifies native dimensions,
edge pixels, overlay removal, and source-context dimensions. Run it with
`QT_SCALE_FACTOR=1.5` to verify export independently of desktop scaling.
For fractional/high-DPI geometry, add `QT_SCALE_FACTOR=1.5` and select only
`tests/modules/video_player/engine/test_quick_geometry.py -k numeric_geometry`; repeat at scale 2.
Other pixel-coordinate tests assume scale 1. Linux/OpenGL results do not certify other graphics APIs.

## Rendering benchmarks

Measure rendering cost and compare it with an older ref using [`tools/bench`](../../tools/bench/README.md).

## Playback memory

To check the [video cache memory](../settings.md#video-cache-memory) figures, generate clips with FFmpeg `testsrc2`
at 1080p and 4K, 30 fps, 20 seconds:
`libx264 -preset ultrafast -crf 28 -g 60 -pix_fmt yuv420p -threads 2` (long-GOP: `-g 300 -sc_threshold 0`). Play
them on the private display without overlays with Manual 1 GiB in fresh processes, recording indexing separately from cached opens.
Play to the end, probe retained and evicted frames, change the allowance during playback, and open and close sources
to check redistribution and cleanup.
