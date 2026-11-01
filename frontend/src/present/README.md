# present

Target design (D22), not built until slice S1 of
[14](../../../docs/rebuild/14-build-order.md#decisions-taken-for-the-overlay-pipeline). Today `../overlay/` prepares
what the backend draws.

Overlay presentation per lane: catalog and filter compilation, evaluation of a Sample Item into Marks, layout for the
lane's cell, picking. Export and `catalog render` run it in an offscreen Electron page, so display and export share it.

- Pure: no DOM and no GL, so it runs in Node tests and the export page. Painting and the glyph atlas stay in
  `../overlay/`.
- The per-frame path retains nothing: typed arrays are reused per lane, strings compare by id, closures are made at
  compile time. It does make short-lived garbage, about 140-300 B per row for a whole catalog, because V8 boxes the
  numbers closures return; the cost to judge is GC pause time in the benchmark, not allocation count.
- Marks carry lengths tagged in pixels or image units, so zoom and resize only lay out again.
- The backend never sees a Lane Style; it receives only the Lane Policy.
