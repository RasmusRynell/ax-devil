# overlays

Feeds a viewer's overlay data and nothing else: loading, statuses, and the items on the playback socket. It never
evaluates a catalog, filters for display or draws; presentation is `frontend/src/present/`.

- Sample Items are per source and shared by every lane viewing it; Frame Maps are per lane, selected by `scene::matching`
  under the lane's Lane Policy. Encoded items are cached once per track and sample, across viewers.
- Items and maps rank against the video frames with `playback::rank` (the data wins ties) and are sent inside the
  sample window of `docs/architecture/overview.md#overlays`, which only the backend implements
  (`fixtures/sample-window/cases.json`, read by Rust only).
- The backend alone decides what a viewer holds: `Source.sent` is what it counts as held, `Source.evicting` what it
  has stopped counting but not yet told the viewer. Control messages (window, evictions) go before data; a sample that
  does not fit `Open.data_bytes` evicts the sent ones that rank after it, or waits without holding back smaller, less urgent
  samples that fit.
- Per-viewer sent sets reuse the frame mechanism but are separate from `playback`'s, which knows no overlays.
