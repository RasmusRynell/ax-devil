# Diagnostics

How to use the **Rendering diagnostics** window to find what makes playback slow. The measurements it shows are
defined in [measurement definitions](../../src/ax_devil/modules/diagnostics/README.md#measurement-definitions).

## Rendering diagnostics

Open **Debug → Debug Metrics** (`Ctrl+D`) to show **Rendering diagnostics**. Select a viewer
by its content/lane name. The rendering view is organized around:

- **Frame pacing:** new-frame and repaint rates, recent superseded submissions, and a graph that switches
  between frame intervals, paint duration, individual paint stages, builds, and submission wait. Click a sample to inspect it.
- **CPU paint cost:** compare new frames, repaints, or all paints. The four indented stages sum to CPU paint
  for each sample. Build costs expand separately: drawing preparation is nested inside preparation;
  scene filtering can precede painting. Last operation/median/p95/max and sample counts use actual measured operations in the selected recent paints.
- **Overlay workload:** the last paint's entity/primitive counts, target size, cache status, rebuild causes,
  and recent prepared-drawing cache reuse. Frame identities and lifetime superseded counts expand under Frame details.

New-frame intervals measure consecutive changed-frame CPU completions. They include pauses and seeks;
they do not infer a playback deadline or actual screen presentation. Repaints do not shorten the interval.
A dash means no measured operation, not zero milliseconds. Idle viewers keep their last frame/workload,
while recent timing tables become empty and rates decay to zero.

History covers the recent 10 seconds, capped at 600 samples per viewer. If the cap shortens the observed
interval, the UI reports its actual length. Superseded submissions have a separate 10-second counter
aggregated into 100 ms buckets, plus a lifetime total. They are not decoder drops.

**Freeze view** holds the displayed snapshot while playback and capture continue. Selection, graph mode,
and workload comparison remain usable on the frozen data. Export saves the frozen snapshot when frozen,
and current data otherwise. **Reset history** resumes the live view and clears rendering measurements only.
Turning Collect Debug Metrics off or on resets the rendering observation interval; source links survive reset.

**Sources & caches** shows independently timestamped source observations. Expand a source and optionally
filter to those owned by the selected viewer. Live controllers link stream/synchronizer instances; offline lanes
link their overlay provider. Shared video caches remain application-wide. Source identities do not depend on
names, so two identically named viewers cannot overwrite each other's source observations. Each field has
its own monotonic observation time; updating one field does not refresh the age of its neighbors.

Cache diagnostics show frame count and reserved bytes against the byte budget, excluding decoder/display memory.
The UI abbreviates long range lists; export retains every range.

## Investigate a spike

1. Choose the relevant measurement in Timing history, then click the spike or **Inspect largest**.
   Selection uses both time and height, so nearby samples at different heights remain selectable.
   The visible snapshot freezes immediately; playback and capture continue.
2. **Inspect paint** shows the selected observation, its measured stage costs, and changes from the
   median of up to 60 preceding paints of the same kind (new frame or repaint). The selected paint and
   later paints are excluded. Missing operations remain unavailable, not zero-cost baseline samples.
3. Read the measured findings: largest stage increase, drawing build time/rebuild triggers, target-size
   and submitted-workload changes. Workload columns compare the preceding paint of the same kind.
   These identify measured contributions and changed inputs; they do not prove an unobserved root cause.
4. For frame intervals, a disjoint breakdown separates time before submission, submission-to-paint wait,
   and painting when all boundaries exist and do not overlap. Before-submission time includes intentional
   pacing, pause, seek, and upstream work. It is not automatically a renderer stall or a dropped frame.
5. Step through **Previous paint / Next paint**, or use arrow keys while the graph has focus, to compare
   neighbors. Historical source context is retained per paint, with each field's time relative to paint
   completion. Later source updates or closure cannot replace this evidence. Source context is captured
   just after paint, outside the paint timer; fields may belong to different frames or postdate completion.
6. **Copy report** copies the findings and evidence. Export includes the frozen history and the selected
   viewer, sample index, and measurement, so the exact investigation can be reconstructed.
7. If the remaining work is opaque, **Investigate with recording** opens the existing Recording tab.
   Start capture, reproduce the problem, and stop/save. It cannot recover stacks for an earlier spike,
   does not attribute CPU usage, and cannot expose native Qt/FFmpeg internals.

Only retained observations can be inspected (recent 10 seconds / up to 600 paints per viewer).
Selecting a sample preserves the current history until Resume live view or Reset history. Switching viewers
clears the inspection selection; changing the graph metric keeps the selected paint. Reset and teardown
release the retained inspection. Application-wide source/cache values remain snapshot-wide; the inspector
uses the source context stored with the selected paint and never substitutes current source values.

## Record a performance timeline

Open **Debug → Debug Metrics**, select **Recording**, click **Start recording**, reproduce the activity, then click **Stop recording**.
Save the recording as JSON for analysis outside the app.
Explore the saved file with the [trace viewer](../../tools/trace-viewer/README.md). What the recording can and cannot
show is described under [performance recording](../../src/ax_devil/modules/diagnostics/README.md#performance-recording).
