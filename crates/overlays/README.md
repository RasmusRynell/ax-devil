# overlays

Target design (D22, D26), not built until slice S1 of
[14](../../docs/rebuild/14-build-order.md#decisions-taken-for-the-overlay-pipeline). Today the crate still draws each
lane's frames into Drawing Instructions.

Feeds a viewer's overlay data and nothing else: loading, statuses, and the items on the playback socket. It never
evaluates a catalog, filters for display or draws; presentation is `frontend/src/present/`.

- Sample Items are per source and shared by every lane viewing it; Frame Maps are per lane, selected by `overlay-sync`
  under the lane's Lane Policy. Encoded items are cached once per track and sample, across viewers.
- Items and maps rank against the video frames with `playback::rank` (the data wins ties) and are sent inside the
  sample window of `docs/architecture/overview.md#overlays`. Its Rust and TypeScript implementations share one fixture
  (target: `frontend/src/present/dataWindow.ts`).
- Per-viewer sent sets reuse the frame mechanism but are separate from `playback`'s, which knows no overlays.
