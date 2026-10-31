# 09 Testing

Owns: test philosophy, pyramid, isolation, the oracle policy and reliability rules. Sources:
`.agents/skills/write-tests/SKILL.md`, `docs/runbooks/testing.md`, `.agents/skills/review/SKILL.md#4-merge-tests-that-protect-behavior`.
Port the write-tests skill and the runbook into the new tree with the new stack's specifics.

## Philosophy (MUST)

Port the write-tests skill as written: delete tests of removed code first; test deep modules and user-consequential
rules once, at the owner; assert outcomes, not wiring; derive expectations from the requirement; fake external
devices and processes at their boundary.

## Pyramid

| Layer | Share | Covers | Current examples |
|-------|-------|--------|------------------|
| Domain / pure | Largest | Matching, sticky selection, live sync, Scene ranking, filters, workspace model and format, bookmark keys, row projection, viewport math, cache identity | `tests/modules/synchronization/`, `tests/modules/workspace/core/`, `tests/modules/scene/model/` |
| Contract | Per plugin and format | Built-in decoders on sample files against golden Scenes; resolver contract; plugin loading and version rejection; format round trips and version rejection | `tests/plugins/`, `tests/modules/plugin_system/` |
| Catalog language conformance | Per language feature | Compiler scope/type/reference checks, kernels, labels, row isolation, schema; representative semantic cases for every schema-v3 operation and primitive | `tests/modules/scene/rendering/` (`test_template_runtime_*.py`, `test_scene_rendering_row_isolation.py`, `test_template_schema.py` there) |
| Rendering | Small | Drawing Instruction output per seeded sheet ([canonical comparison](#oracle-policy)); rendered images inside the new stack (tolerance); native-size export; fractional-scale geometry | `tests/modules/scene/rendering/test_built_in_catalog_rendering.py`, `tests/modules/video_player/engine/` |
| Pipeline integration | Medium | Entry opening to display; fake live transports to synchronized pairs; export end to end; file change to re-resolution | `tests/modules/video_viewer/` |
| Concurrency | Few | Real executors, repeated runs, barriers: job-scope close, cancellation, superseding requests, live queues, destruction on the required executor, shutdown; the stack's sanitizer if it has one | new |
| State model | Few | Seeded random operation sequences checked after every step | `tests/modules/video_viewer/media_tools/test_scene_render_catalog_state_model.py` |
| UI behavior | Few | Fit (no clipped text, both themes, window minimum and 200% zoom), focus and keyboard | `tests/modules/chrome/test_surface_fit.py` |
| Crash safety | Few | Kill the process during a recovery-copy write and during a cache build; the next launch recovers | new |
| CLI | Few | Commands build the right items; headless catalog commands' exit codes | `tests/core/` |
| Installation | Separate target | Real install and plugin lifecycle in isolated storage | `make test-integration` |
| Performance and soak | Scheduled | Benchmarks against a ref (DT-03); playback memory; hours of synthetic live input with bursts: bounded memory, no stall | `tools/bench`, `docs/runbooks/testing.md#playback-memory` |

## Isolation (MUST)

- Each run gets its own temporary home and data folders before app code loads. The suite records the user's real
  config, catalogs and plugin data and fails if they changed (`docs/runbooks/testing.md#tests-never-touch-the-users-files`).
- GUI tests never use the user's session. They run on the stack's offscreen renderer or a private virtual display,
  one per parallel worker so windows never compete for focus. A missing environment fails the run.
  *Current:* Xvfb with Mesa software GL on Linux, started by `tests/__init__.py`; `tools/private_display.py` for
  standalone tools.
- On CI machines without a user session (Windows, macOS runners) the native platform is acceptable, run serially.
- Only built-in plugins load; regular tests are offline and mock RTSP, MQTT and device APIs.
- What the virtual display cannot verify (physical GPU, compositor, window manager) is reported as unrun.

## Oracle policy

- Semantic assertions are written from the requirement and never copied from old output.
- Oracle fixtures are generated in a worktree at the reference commit after a locked dependency sync, with a
  temporary `HOME` and XDG folders, `--config` pointing at a temporary file and the private display tool, so user
  plugins and catalogs never enter them. They are committed with provenance: source commit, environment, generation
  command, intentional deviations.
- Semantic oracle: Drawing Instruction dumps per seeded sheet, decoded Scene dumps (D15) and timestamp alignment
  TSVs (`tools/timestamp_alignment_report.py`). Canonical comparison: exact for draw kinds, the required kind order
  (shapes, text, labels), discrete values, colors, text and routing; order across entities and within a kind is unspecified
  (`docs/domain/invariants.md#rendering`), so both sides are sorted by a canonical key (kind, routing and entity,
  discrete fields) before geometry is compared within a stated tolerance (proposal: 1e-3 px).
- Pixel images from the old app are for human review only. Pixel goldens are produced inside the new stack with
  renderer-specific tolerances.
- When new and old output differ: decide which is right against the requirement, record the decision as a
  deviation, update the fixture. A known old bug is never frozen into a fixture.

## Fixtures

- Tiny generated videos from a factory keyed by duration, FPS and codec, including variable frame rate; each test
  owns its readers and caches.
- An immutable small catalog for management tests; each test gets its own empty user catalog store.
- Small real sample files per built-in decoder, with their license noted.

## Reliability (MUST)

- Time and scheduling are injected ([03](03-runtime.md#time-and-scheduling-must)); policy tests use a virtual clock
  and a controllable executor instead of sleeps, inline execution only where scheduling is irrelevant.
- Wait for observable state with bounded waits; never assert elapsed time as proof. A cache hit is shown by retained
  frames and no extra decode.
- A flaky test either protects a real requirement (find the cause, product or synchronization) or is deleted. Never
  raise timeouts, relax comparisons or retry.

## UI verification

1. The screenshot harness (DT-02) renders the main surfaces in both themes, at normal zoom and 200%, wide and narrow.
2. Fit measured at the window minimum and at 200% zoom.
3. Every window joins the fit test; a guard test fails until it is listed or exempted with a reason
   (`.agents/skills/write-ui/SKILL.md#check-it`).

## CI

Port the jobs in `docs/runbooks/testing.md#running-tests`: quality (checks, suite, install smoke, build), suite per
supported OS (D2), lowest allowed dependencies, clean-environment release check on tags (DT-05).
