# Testing

## Running tests

```bash
make test                                       # Regular offline suite; Qt defaults to offscreen
QT_QPA_PLATFORM=offscreen make test-integration  # Real development, pip/tool, and upgrade checks; may download packages
make check                                     # Formatting, linting, logging conventions, and strict types
```

`make test` distributes whole files across available CPUs, capped at eight workers. Use
`make test TEST_WORKERS=0` for serial debugging or set a smaller worker count on constrained machines.
Each worker owns a separate temporary home and app storage; subprocesses inside a worker reuse that worker's home.
Focused `uv run pytest ...` commands and the installation integration suite stay serial.

To run a specific test file or test:

```bash
QT_QPA_PLATFORM=offscreen uv run pytest tests/modules/workspace/test_content.py
QT_QPA_PLATFORM=offscreen uv run pytest tests/modules/workspace/test_content.py::test_specific_case -v
QT_QPA_PLATFORM=offscreen uv run pytest tests/ -k "keyword"
```

Plugin runtime checks:

```bash
QT_QPA_PLATFORM=offscreen uv run pytest tests/modules/plugin_system tests/modules/plugin_installation
uv run ax-devil plugins --help
uv run ax-devil --help
```

Standalone trace-viewer checks:

```bash
make -C tools/trace-viewer check  # Python relay/schema tests and Node parser/navigation tests
```

The combined CI quality job runs these checks too. The tool's MCP dependencies remain separate from the application
environment.

The regular suite covers plugin installation with uv mocked, plus real entry-point discovery and decoding of the
minimal package from `.agents/skills/write-plugin/reference.md` in fresh Python processes.

`make test-integration` installs the app and that plugin for real (editable, `uv tool install`, and pip from wheels)
outside the checkout with isolated storage, then checks launching, decoding, upgrades, rollback, refresh at launch and
removal. It may download and build packages, needs the same system libraries as a normal installation, and publishes
nothing.

CI uses two jobs for pull requests: `quality` shares one Python 3.12 environment for application checks,
trace-viewer checks, the regular suite, installation smoke tests, and package builds; `test` runs the regular
suite on the minimum supported Python 3.10. Pushes to `main` and release tags also run `lowest`, which tests
Python 3.10 with the oldest allowed dependencies (`uv pip install --resolution lowest-direct -e . --group dev`).
Superseded runs on the same branch or PR are canceled; release runs are not interrupted.
Pytest excludes the `integration` marker by default; `-m integration` selects it explicitly.

## Package artifacts

`uv build` builds the wheel and source distribution. CI runs it on pull requests, main pushes, and version tags.
Artifacts are uploaded only for release tags. On a `v*` tag, after all three CI jobs pass, CI runs
`tools/release-check/run.sh` and publishes the built artifacts to PyPI through trusted publishing.
A published release must remain available to the plugin resolver at its exact version.

`tools/release-check/run.sh [tool|pip|py310|source]` needs Docker. It installs the candidate in fresh Ubuntu 24.04
containers by running the documented prerequisite and source commands as written. The `tool`, `pip`, and `py310`
scenarios install the built wheel, with a local directory standing in for PyPI and every dependency coming from PyPI;
they render catalogs, install the documented plugin, play a generated video offscreen, upgrade the app, and check that
plugins follow. `source` clones the candidate, runs `make check`, `make test`, and `make test-integration`, and checks
that editable plugin edits apply without reinstalling.

## GUI verification

All agent verification runs away from the user's desktop. Use the offscreen commands above for
normal tests and visual checks. Save `QWidget.grab()` or `QQuickWindow.grabWindow()` output to
`tmp_path` or `/tmp` and inspect that image. Send interactions through Qt test events.
For application UI, see [the write-ui skill's checks](../../.agents/skills/write-ui/SKILL.md#check-it).

For native-window and OpenGL checks, run the existing tests on a private Xvfb display:

```bash
env -u WAYLAND_DISPLAY -u QT_QPA_PLATFORMTHEME -u QT_QUICK_BACKEND -u QSG_RHI_BACKEND \
  -u QT_WIDGETS_RHI -u QT_WIDGETS_RHI_BACKEND AX_DEVIL_TESTS_QPA_PLATFORM=xcb \
  __GLX_VENDOR_LIBRARY_NAME=mesa LIBGL_ALWAYS_SOFTWARE=1 \
  xvfb-run -a -s "-screen 0 1920x1080x24 -nolisten tcp" uv run pytest \
  tests/modules/catalog_viewer/test_catalog_commands.py \
  tests/modules/catalog_viewer/test_catalog_viewer_window.py \
  tests/modules/video_player/engine/test_image_renderer.py \
  tests/modules/video_player/test_quick_renderer.py \
  tests/modules/video_player/test_lane_fullscreen.py \
  tests/modules/workspace/test_offline_playback.py
```

Run these groups together so catalog window teardown is followed by image export and renderer creation in the same
process, without globally forcing widget RHI: this also checks main-window preparation and raster Settings/menu
windows. The close/reopen test waits for preview preparation and native destruction and checks that the reopened
viewer can render. This setup uses Mesa software OpenGL, overriding inherited desktop driver selection: an NVIDIA
GLX override on Xvfb can leave invalid contexts after window teardown. It still exercises Qt's OpenGL scene graph,
unlike the normal offscreen Qt Quick software backend. It does not verify the physical GPU or its driver.

Install prerequisites once: `sudo pacman -S --needed xorg-server-xvfb xorg-xauth` on Arch/Omarchy,
or `sudo apt install xvfb xauth` on Debian/Ubuntu. `xvfb-run` allocates a private display and
cleans up its server and authority file when the command ends. Standalone GUI tools use the same
`QT_QPA_PLATFORM=xcb xvfb-run -a` prefix and must close their windows and workers when finished.
The pytest suite isolates app storage too; standalone app checks must use temporary settings and fixtures.

Xvfb has no window manager and may use software OpenGL. Window-manager interaction, Wayland, and
physical GPU checks need a separate test session or machine with its own display and input.
Never launch verification windows, send input, or capture screenshots on the user's desktop.
If the required isolated environment is unavailable, report the check as unrun and its coverage limit.
Real-device checks use configured test devices; the offline suite mocks external services.

## Test organization

```
tests/
├── conftest.py                    Shared fixtures (temp dirs, plugin loading, Qt offscreen)
├── core/                          Root CLI, package metadata, and `ax_devil.core` tests
├── modules/                       Concept-owned module tests for workspace, viewer workflow, scene, sources, sync, cache, and playback
├── plugins/                       Plugin decoder and playlist resolver tests
└── helpers/                       Test utility modules
```

Config and settings tests live under `tests/modules/settings/`.

## Key conventions

- **Regular tests stay offline.** Only the explicit installation integration test may download packages. Mock all external services (RTSP, MQTT, device APIs).
- **Qt offscreen.** `tests/__init__.py` sets `QT_QPA_PLATFORM=offscreen` before importing Qt or application modules,
  replacing inherited desktop platform settings, so plain `uv run pytest` and CI use the same platform. Offscreen runs
  clear `QT_QPA_PLATFORMTHEME` so a desktop theme cannot initialize GTK and try to open the user's display. Set
  `AX_DEVIL_TESTS_QPA_PLATFORM=xcb` only for the private-display checks above. Create applications through pytest-qt's
  `qapp`/`qtbot`, never during collection.
- **Plugins isolated.** A session-scoped fixture loads built-ins while installed external entry points are patched out.
- **Qt teardown.** Register widgets with `qtbot.addWidget()`. Register only top-level widgets: Qt deletes children with their parent, and a registered child whose parent was garbage-collected first makes pytest-qt close an already-deleted widget. The shared teardown hook drains Qt's deferred deletions after pytest-qt closes widgets, keeping native destruction on the GUI thread and out of later tests.
- **Temp directories.** Use pytest's `tmp_path` for file-system tests. `temp_dir` is an alias; `test_temp_root` supplies a session directory for shared generated media. Pytest manages their retention and cleanup.
- **Catalog fixtures.** Shared UI fixtures give each test its own empty user catalog store. For management, selection and validation tests, use `small_catalog_path` as `create_catalog(..., base_catalog_path=small_catalog_path)` or get fresh document data from `tests.catalog_helpers.catalog_document()`. The source bytes are immutable; each user copy still passes real schema validation and compilation. Keep packaged-catalog checks for its schema, sheets and rendered output. Tests must never write to the built-in catalog path. `test_scene_render_catalog_state_model.py` checks selections and selectors after every step of named scenarios and seeded random sequences; add a scenario there for a state bug that focused tests do not cover.
- **Prefer controller-level tests** over widget-heavy tests. `pytest-qt` is available but keep widget interactions minimal.
- **Isolated and fast.** Each test should be independent. No shared mutable state between tests.

## Writing a new test

Each new test should protect a meaningful failure: wrong frames or overlays, lost settings, leaked workers,
failed plugin loading, or damaged output. Prefer strengthening an existing case over adding another smoke test.
Assert observable output using explicit expectations; avoid constructor echoes, deleted-name checks and exact
internal call sequences. Fake external transports or processes, while keeping owned components real where practical.
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
`~/.ax_devil/configs`, `render_catalogs` and `plugins` and the plugin data folder before the run, and fail it if any of
them changed. After each test the shared configuration is pointed back at the suite's own file, so a test that switches
it cannot affect later ones. A test that needs files uses `tmp_path`.

## Quick renderer verification

`tests/modules/video_player/test_quick_renderer.py` exercises rendered output, text/path
ordering, opacity, texture replacement, software rendering, and reparenting.
`tests/modules/video_player/engine/test_image_renderer.py` verifies native dimensions,
edge pixels, overlay removal, and source-context dimensions. Run it with
`QT_SCALE_FACTOR=1.5` to verify export independently of desktop scaling.
Normal tests use the software scene graph. For native-window verification after rendering or startup
changes, use the isolated command in [GUI verification](#gui-verification).
For fractional/high-DPI geometry, add `QT_SCALE_FACTOR=1.5` and select only
`tests/modules/video_player/test_quick_renderer.py -k numeric_geometry`; repeat at scale 2.
Other pixel-coordinate tests assume scale 1. Linux/OpenGL results do not certify other graphics APIs.

## Rendering benchmarks

Expected costs are in [Draw System performance](../architecture/draw-system.md#performance). On Xvfb, OpenGL
composition adds about 24 ms per round because each frame is copied back to the X server; a real display differs.
`--gpu-sync` adds about 0 ms there, and software-backend rounds spike near 170 ms regardless of the change under
test. `tools/benchmark_quick_renderer.py` measures the Quick renderer with synthetic shapes. For a native-window
comparison on a private display, run each backend separately. These results do not certify physical GPU performance:

```bash
QT_QPA_PLATFORM=xcb xvfb-run -a uv run python tools/benchmark_quick_renderer.py --moving --text
QT_QPA_PLATFORM=xcb xvfb-run -a uv run python tools/benchmark_quick_renderer.py --software --moving --text
QT_QPA_PLATFORM=xcb xvfb-run -a uv run python tools/benchmark_quick_renderer.py --changing-content --mixed --text
QT_QPA_PLATFORM=xcb xvfb-run -a uv run python tools/benchmark_quick_renderer.py --moving --text --fixed-image
```

This synthetic workload reports process CPU consumed from submission through render
submission, including Quick scene graph work. It separately reports elapsed wall
time, which includes event-loop/vsync waiting. It excludes source generation,
framebuffer readback, GPU completion, and display latency; it is not a playback FPS benchmark. Omitting
`--moving` measures resource reuse with a changing video frame and static overlays.

The benchmark also reports CPU scopes for GUI preparation, scene graph synchronization
(`beforeSynchronizing` to `afterSynchronizing`), and render submission (`beforeRendering`
to `afterRendering`). These are process CPU durations, not GPU timestamps. Their medians
do not sum to the total median, and work outside these scopes remains in the total.
The benchmark window stays above other windows to avoid occlusion suppressing rendering; keep it on the private display.
`--mixed` includes boxes, circles, lines, polygons, and polylines; `--changing-content`
changes dimensions and label text as well as positions. `--fixed-image` retains the
video texture while updating overlays. Run cases sequentially without competing work.

`tools/benchmark_catalog_lanes.py` measures production viewers rendering the packaged catalog, with a new moving Scene
and video image per lane every round. A round ends when the window has composited it. It reports the catalog and binding
stages, each lane's whole Qt Quick frame (`quick_frame`: polish, sync, render and frame end, with `scene_sync` and
`render_submission` as parts of it), the window's composition and flush (`compose`), and their sum (`frame_total`),
which should match wall time and process CPU; `uncomposed_rounds` counts rounds that ended without composition.
`--gpu-sync` also waits for the GPU after each round (`gpu_wait`); that serializes CPU and GPU, so leave it off when
comparing CPU cost. `tools/benchmark_template_runtime.py` isolates catalog evaluation and drawing preparation offscreen.

```bash
QT_QPA_PLATFORM=xcb xvfb-run -a uv run python tools/benchmark_catalog_lanes.py --lanes 4 --entities 250
QT_QPA_PLATFORM=xcb xvfb-run -a uv run python tools/benchmark_catalog_lanes.py --lanes 4 --entities 250 --changing-scores
QT_QPA_PLATFORM=offscreen uv run python tools/benchmark_template_runtime.py --iterations 30 --entities 250
```

Use `--catalog FILE` to compare appearances and `--changing-scores` to exercise label texture replacement rather
than only moving stable labels. Compare changes with the same catalog, backend, viewport and workload, sequentially
without other verification running. Confidence values cycle through sixty two-decimal values; this is a repeatable
cache workload, not a claim about every camera's score distribution. Label tests in `test_quick_renderer.py` cover
shared textures, bounded eviction, disappearing instances, clearing and scene-graph recreation. Run them on both
the offscreen software backend and the private OpenGL display to check native resource destruction.

## Playback memory

To check the [video cache memory](../settings.md#video-cache-memory) figures, generate clips with FFmpeg `testsrc2`
at 1080p and 4K, 30 fps, 20 seconds:
`libx264 -preset ultrafast -crf 28 -g 60 -pix_fmt yuv420p -threads 2` (long-GOP: `-g 300 -sc_threshold 0`). Play
them offscreen without overlays with Manual 1 GiB in fresh processes, recording indexing separately from cached opens.
Play to the end, probe retained and evicted frames, change the allowance during playback, and open and close sources
to check redistribution and cleanup.
