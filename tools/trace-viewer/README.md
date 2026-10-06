# Trace viewer

A standalone timeline and per-thread flame graph for performance recordings. Open `index.html`
in a browser and drop a JSON recording onto it. No build, server, or upload is required.
The viewer is a separate developer tool, not bundled with the application or its wheel.
It reads ax-devil stack samples and VizTracer/Chrome complete-duration events (`ph: "X"`).

To record, open **Debug Stats** in ax-devil, click **Start recording**, reproduce the activity,
and click **Stop recording** to save. See [recording details](../../src/ax_devil/modules/diagnostics/README.md#record-a-performance-timeline).

## Explore a recording

- Switch between **Timeline** for chronological activity and **Flame graph** for bundled stack paths.
- Click a block for its function, source path, thread, and timing. Double-click to focus it.
- Search names or paths in the sidebar. Results summarize the whole recording.
- Click a thread heading to collapse its stack.

In the timeline, drag to pan; Ctrl/Cmd + wheel zooms at the pointer. Wheel scrolls vertically and
Shift + wheel pans horizontally. Click the overview to move the visible interval. **Fit recording**
resets the range; **Focus activity** shows the central 98% of call starts. **Next sample/call** steps
through occurrences of the selected function. Keyboard: arrows pan, `+`/`-` zoom, `F` fits.

Flame graphs cover the **whole recording**, regardless of timeline zoom. Identical caller paths
merge within each thread; different callers and threads stay separate. Each thread is normalized
independently. Width represents recorded time; horizontal position does not represent time.
Double-click a branch to focus it, **Up one level** to step back, or **Reset focus** to restore all
threads. **Show in timeline** jumps to the longest occurrence of that stack path.

## Connect an AI agent

From the repository root:

```bash
uv run tools/trace-viewer/server.py
```

Open **http://127.0.0.1:8766**, then load a recording. Register **http://127.0.0.1:8766/mcp** in your
agent as a **Streamable HTTP MCP server**. `--port 8767` changes both endpoints. The **AI connected**
badge means the browser is connected to the bridge. Selections are shared automatically while the
tab stays open. Opening the HTML file directly provides the viewer without MCP.

| Tool | Purpose |
| --- | --- |
| `get_view` | Start here: current selection, visible interval, recording quality, threads, and open tabs. |
| `search_flame` | Find expensive stack paths across the whole recording, separately per thread. |
| `search_timeline` | Find occurrences by time interval, function/path, thread, and duration. |
| `inspect` | Read one result, its callers, and a page of direct children. |
| `focus` | Show an item or time interval in the user's UI. The only tool that navigates. |

The server supplies startup instructions, described input/output schemas, and
[AGENT_GUIDE.md](AGENT_GUIDE.md) as MCP resource `trace://guide`. Results default to 5 items, capped
at 50. Recording IDs invalidate stale queries after a new file loads; session IDs distinguish tabs.
Restart the server and reconnect the MCP client after updates to refresh tool discovery.

The server listens on loopback and validates browser origins. Its dependencies are isolated by
`uv`; none are added to the desktop app. Parsing and analysis stay in the browser. The bridge
relays bounded requests and results, and serves no arbitrary local files.

## Interpret the timings

Sampled recordings observe Python stacks at a target 100 Hz. Each sample represents at most one
nominal interval, clipped at the next sample or recording end. Unobserved gaps stay empty. The
banner reports temporal coverage, observed sample rate, and the largest gap. The GIL can delay
sampling; short calls and native internals can be missed. Sightings are not invocation counts.

Total time includes descendants; self time excludes recorded descendants. Both include waits and
are **not CPU measurements**. Parent/child totals and overlapping threads are not additive.
Timeline searches return overlapping occurrences with **unclipped** durations.

Detailed traces use recorded call durations. Overflowed traces can have missing history and inflated
self time; the viewer flags overflow without reconstructing missing activity. Unsupported events
are counted and skipped. Invalid files leave the previous recording open.

Parsing and indexing run in a Web Worker. Drawing uses compact arrays, binary searches, and pixel
aggregation; zoom in to separate tiny blocks. Parsing memory still scales with recording size.

## Development

Keep the viewer self-contained in `index.html`: the parser worker builds the timeline/flame indexes,
`TraceAgentAPI` queries those indexes, and the UI renders them. `server.py` exposes MCP tools and
relays requests over WebSocket; `schemas.py` describes and validates their results. Both public
search tools use the browser's internal `search` operation with an explicit mode.

From the repository root, with Node.js 18+ and the project's development dependencies installed:

```bash
make -C tools/trace-viewer check
```

This runs standalone Python formatting, lint, strict typing, MCP transport/discovery tests, and
Node parser/query tests using synthetic recordings. Run `make check` and `make test` for the desktop
app, including recorder lifecycle, export retries, sampling failures, and Qt-thread coverage.
