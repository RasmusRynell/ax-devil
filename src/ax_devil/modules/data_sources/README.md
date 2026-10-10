# data_sources module

Purpose: everything that produces frames and overlays: the offline file frame source and its decoder cache, file
overlay providers and their indexed stores, Scene history, and the live RTSP, MQTT and DataHub transports under
`live/`. Shared contracts are in `base.py`. The source types and their lifecycles are listed in the
[architecture overview](../../../../docs/architecture/overview.md#data-sources); the rules other modules rely on
are in [Frame Identity](../../../../docs/domain/invariants.md#frame-identity) and
[Data Pipeline](../../../../docs/domain/invariants.md#data-pipeline).

## Rules

### Offline frame cache

`video_cache_memory.py` binds the Playback cache preference to the process-wide `FrameCachePool`, which splits one
allowance equally across open sources (lanes sharing a source count once) and resizes immediately on open, close or
preference change. Rebalancing shrinks before growing, so the sum of shares never exceeds the allowance. Auto is 25%
of RAM at startup; neither mode preallocates. This limits cache reservations, not RSS.

- A cached frame reserves the larger of its source planes and full RGB24; conversion does not change the reservation.
- Prefetch may evict older history, but never a frame between the playhead, the gap it is filling, and the frame it
  admits. Its window follows the read direction, and it jumps back to a keyframe only when the frames from it to the
  playhead fit the budget, so a keyframe group is never decoded repeatedly to keep one more frame.
- Close releases the cache and share even if the QObject is still alive. Release cache locks before entering pool
  lifecycle methods; the pool may take several cache locks while rebalancing.

### File overlay providers

- Providers retain only the two most recently used decoded scenes, keyed by resolved source timestamp. Larger decoded
  histories cause garbage-collection pauses on the shared GUI thread.
- Scene history is stored as parsed records with plain tuples of frame indices, never JSON, for the same reason.
- An opened frame cache keeps reading through its retained file handle until closed, even if the file is removed.

### Live transports

- RTSP connects on its worker thread: `play()` never waits for the camera, and `stop()` cancels a pending connect.
- DataHub uses HTTPS/WSS by default with certificate verification deliberately disabled for this development tool.
  HTTP/WS is an explicit choice; there is no exception state or automatic downgrade, and redirects are rejected. The
  session-token request answers the advertised scheme, Digest before Basic, and a rejected Digest never falls back to
  Basic, since Basic over HTTP exposes credentials.
- DataHub sample reception waits until data arrives or shutdown cancels it; idle silence is not a transport error.
  Only connection setup and complete protocol requests have deadlines.
