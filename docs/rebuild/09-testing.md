# 09 Testing

Owns: the test pyramid, with the reference's tests as examples, and the oracle policy. How to write, isolate and run
tests is in the [write-tests skill](../../.agents/skills/write-tests/SKILL.md) and the
[testing runbook](../runbooks/testing.md).

## Pyramid

| Layer | Share | Covers | Current examples |
|-------|-------|--------|------------------|
| Domain / pure | Largest | Matching, sticky selection, live sync, Scene ranking, filters, workspace model and format, bookmark keys, row projection, viewport math, cache identity | `tests/modules/synchronization/`, `tests/modules/workspace/core/`, `tests/modules/scene/model/` |
| Contract | Per plugin and format | Built-in decoders on sample files against golden Scenes; resolver contract; plugin loading and version rejection; format round trips and version rejection | `tests/plugins/`, `tests/modules/plugin_system/` |
| Catalog language conformance | Per language feature | Compiler scope/type/reference checks, expressions, labels, row isolation, schema; representative semantic cases for every schema-v4 rule, layer and mark kind | TypeScript tests beside the compiler in `frontend/src/present/` (target); the reference's are `tests/modules/scene/rendering/` |
| Rendering | Small | Marks per seeded sheet, projected to the oracle's canonical form ([canonical comparison](#oracle-policy)); rendered images inside the new stack (tolerance); native-size export; fractional-scale geometry | `frontend/src/present/` oracle tests (target); the reference's are `tests/modules/scene/rendering/test_built_in_catalog_rendering.py`, `tests/modules/video_player/engine/` |
| Pipeline integration | Medium | Entry opening to display; fake live transports to synchronized pairs; export end to end; file change to re-resolution | `tests/modules/video_viewer/` |
| Concurrency | Few | Real executors, repeated runs, barriers: job-scope close, cancellation, superseding requests, live queues, destruction on the required executor, shutdown; the stack's sanitizer if it has one | new |
| State model | Few | Seeded random operation sequences checked after every step | `tests/modules/video_viewer/media_tools/test_scene_render_catalog_state_model.py` |
| UI behavior | Few | Fit (no clipped text, both themes, window minimum and 200% zoom), focus and keyboard | `tests/modules/chrome/test_surface_fit.py` |
| Crash safety | Few | Kill the process during a recovery-copy write and during a cache build; the next launch recovers | new |
| CLI | Few | Commands build the right items; headless catalog commands' exit codes | `tests/core/` |
| Installation | Separate target | Real install and plugin lifecycle in isolated storage | `make test-integration` |
| Performance and soak | Scheduled | Benchmarks against a ref (DT-03), with the presentation benches run in Node (no retained growth over hundreds of frames, minor-GC pause time within budget, an order-of-magnitude ceiling) and GPU numbers measured by hand on real GPUs; playback memory; hours of synthetic live input with bursts: bounded memory, no stall | `tools/bench`, `docs/runbooks/testing.md#playback-memory` |

## Oracle policy

- Semantic assertions are written from the requirement and never copied from old output.
- Oracle fixtures are generated in a worktree at the reference commit after a locked dependency sync, with a
  temporary `HOME` and XDG folders, `--config` pointing at a temporary file and the private display tool, so user
  plugins and catalogs never enter them. They are committed with provenance: source commit, environment, generation
  command, intentional deviations.
- Semantic oracle: the reference's Drawing Instruction dumps per seeded sheet (the oracle's canonical form, which Marks
  are projected to), decoded Scene dumps (D15) and timestamp alignment TSVs (`tools/timestamp_alignment_report.py`).
  Canonical comparison: exact for draw kinds, discrete values, colors, text and routing; order across entities and
  within a kind is unspecified (`docs/domain/invariants.md#rendering`), so both sides are sorted by a canonical key
  (kind, routing and entity, discrete fields) before geometry is compared within a stated tolerance (proposal: 1e-3 px).
  Where v4 differs on purpose (layer order, label placement lists) the difference is listed per case as a deviation,
  never tolerated silently.
- Pixel images from the old app are for human review only. Pixel goldens are produced inside the new stack with
  renderer-specific tolerances.
- When new and old output differ: decide which is right against the requirement, record the decision as a
  deviation, update the fixture. A known old bug is never frozen into a fixture.
