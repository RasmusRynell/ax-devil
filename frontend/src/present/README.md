# present

Overlay presentation per lane: catalog and filter compilation, evaluation of a Sample Item into Marks, layout for the
lane's cell, and picking (`pick/`). Export and `catalog render` run it in the Render Page, so display and
export share it.

- Pure: no DOM and no GL, so it runs in Node tests and the export page. Painting and the glyph atlas stay in
  `../overlay/`.
- The per-frame path retains nothing but the caches below and the text run cache of each atlas page: typed arrays are
  reused per lane, strings compare by id, closures are made at compile time. It does make short-lived garbage, about
  140-300 B per row for a whole catalog, because V8 boxes the numbers closures return; the cost to judge is GC pause time in the benchmark, not
  allocation count.
- Marks carry lengths tagged in pixels or image units, so zoom and resize only lay out again. `LayoutView.zoom` is the
  page zoom: pixel lengths are divided by it, so overlays keep their size on screen.
- The backend never interprets a Lane Style; export carries it frozen.
- A lane remembers its last result (`lane.ts`): the same sample, catalog, style and filter skip filter and evaluate;
  the same displayed size, device ratio, zoom and atlas page skip layout too. Pan and opacity are not inputs. The key
  is the sample object, so whoever decodes must hand over a new object for new data.
- Picking reads the lane's kept rows, not its marks: an object a catalog does not draw or a feature hides stays
  pickable, and one the filter dropped does not.
- A `LayoutView.visible` region lets layout leave out marks that cannot reach it (a zoomed image). `LanePresenter`
  lays out half a window more on each side, so a small pan does not lay out again.
