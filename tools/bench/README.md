# Rendering benchmarks

`uv run python -m tools.bench <command> --help`. Every measurement runs in a fresh child process with a temporary
HOME, so it never touches your settings or caches. `--repeat N` runs it N times and reports the median of the medians;
`--json FILE` appends each result as one JSON line (`schema`, `ref`, `commit`, `dirty`, `args`, `env`, `median_ms`,
`p90_ms`, `lane_fps`, `process_cpu_ms`).

| Command | Measures |
|---------|----------|
| `prepare` | Catalog evaluation and drawing preparation per frame, offscreen with no GPU. `--profile N` prints the N hottest functions. |
| `lanes` | `--lanes` production viewers in one window, each fed a new moving Scene and image per round: time per stage, process CPU and unpaced `lane_fps`. Options cover `--decode`, `--interaction pan\|zoom`, `--fps`, `--changing-scores`, `--gpu-sync`, `--hide-feature`. |
| `sheets OUT` | Renders catalog sheets to `OUT/<catalog>/<sheet>.png` and times them. |
| `compare REF -- <command>` | Runs the command on REF and on the working tree alternately (A/B/A/B, `--repeat` 5) and prints stage, base, head, delta. For `sheets` it also prints the share of pixels that differ clearly. |

`--catalog` takes a built-in name, a catalog file, or `all`. Stages are timed through the table in `stages.py`;
a stage that does not exist in the measured code (an older ref) shows `-` and is null in JSON.

Displays: `lanes` and `sheets` need a GL context, so they start a private Xvfb (never your desktop). The default
`--display private` keeps the inherited GL driver, `mesa` forces software llvmpipe, and `real` uses your own display
and is for you to run, not for agents or CI. On a private display presentation is a `glFlush` (`swap_shim.c`, compiled
on first use with `cc`) because Xvfb's buffer swap adds 24 to 30 ms per round; `--real-swap` keeps it. Results do not
certify performance on another machine; compare numbers measured on the same one.

`compare` checks REF out as a detached worktree under `$XDG_CACHE_HOME/ax-devil-bench/<commit>`, reused by later runs,
and runs it with this checkout's toolkit (`PYTHONPATH` onto its `src`, or its own `uv sync` when `uv.lock` differs).
`compare REF --clean` removes the worktree.
