# present

Overlay presentation per lane: catalog and filter compilation, evaluation of a Sample Item into Marks, and layout for
the lane's cell. Picking is not built (S5). Export and `catalog render` will run it in an offscreen Electron page
(S6), so display and export share it.

- Pure: no DOM and no GL, so it runs in Node tests and the export page. Painting and the glyph atlas stay in
  `../overlay/`.
- The per-frame path retains nothing but the caches below and the text run cache of each atlas page: typed arrays are
  reused per lane, strings compare by id, closures are made at compile time. It does make short-lived garbage, about
  140-300 B per row for a whole catalog, because V8 boxes the numbers closures return; the cost to judge is GC pause time in the benchmark, not
  allocation count.
- Marks carry lengths tagged in pixels or image units, so zoom and resize only lay out again.
- The backend never sees a Lane Style; it receives only the Lane Policy.
- A lane remembers its last result (`lane.ts`): the same sample, catalog, style and filter skip filter and evaluate;
  the same displayed size, device ratio, zoom and atlas page skip layout too. Pan and opacity are not inputs. The key
  is the sample object, so whoever decodes must hand over a new object for new data.
- A `LayoutView.visible` region lets layout leave out marks that cannot reach it (a zoomed image). `LanePresenter`
  lays out half a window more on each side, so a small pan does not lay out again.
