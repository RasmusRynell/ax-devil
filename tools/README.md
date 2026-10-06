# Developer tools

Standalone scripts for diagnosing and measuring ax-devil. They are not part of the application or its wheel, and the
app never imports them. Run them from the repository root. `make check` lints and type-checks the scripts in this
folder; `make -C tools/trace-viewer check` covers the trace viewer.

| Tool | Use it to |
|------|-----------|
| [`trace-viewer/`](trace-viewer/README.md) | Explore a performance recording from **Debug → Debug Metrics** as a timeline and flame graph, optionally with an AI agent. |
| `benchmark_quick_renderer.py` | Measure Qt Quick renderer CPU cost with synthetic shapes. |
| `benchmark_catalog_lanes.py` | Measure production viewers rendering the packaged catalog across several lanes. |
| `benchmark_template_runtime.py` | Measure catalog evaluation and drawing preparation offscreen. |
| `timestamp_alignment_report.py` | Write a TSV of every video frame and overlay timestamp, decoded with the app's own decoders and matched with its policies. |
| [`release-check/`](release-check/run.sh) | Install a release candidate in clean Ubuntu containers as documented; see [Package artifacts](../docs/runbooks/testing.md#package-artifacts). |
| `ui_screenshots.py` | Save offscreen screenshots of the main UI surfaces in both themes and two text sizes, to compare a UI change; see the [write-ui skill](../.agents/skills/write-ui/SKILL.md#check-it). |
| `show_timestamps.py` | Print a quick timestamp table for a video and a VOD `.od` or MOTE `.xml` file. Needs only PyAV, so it also runs outside this project. |

How to run the benchmarks and read their results: [Rendering benchmarks](../docs/runbooks/testing.md#rendering-benchmarks).

```bash
uv run python tools/timestamp_alignment_report.py --video VIDEO --overlay OVERLAY --handler HANDLER \
    --output report.tsv --tolerance-us 0
uv run python tools/show_timestamps.py VIDEO DATA
```

Run any script with `--help` to see all of its options.
