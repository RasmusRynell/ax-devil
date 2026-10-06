# Offline playback memory

**Settings → General → Playback → Memory for video caching** controls one shared allowance for all open offline
video/image-sequence sources. More memory keeps more frames ready for seeking. The cache fills only as
needed; selecting a large allowance does not allocate it in advance.

- **Auto (default):** **25% of available RAM measured at startup**, with no fixed ceiling. For example,
  16 GiB available gives a 4 GiB total allowance. Linux's `MemAvailable` includes reclaimable memory;
  this is not installed RAM or just completely free pages. The startup allowance stays stable rather
  than changing as the cache fills. If detection fails, Auto falls back to 1 GiB.
- **Manual:** choose a total allowance in GiB. For example, 1 GiB gives one source 1 GiB, two sources
  512 MiB each, or four sources 256 MiB each. The allowed range is 0.25–1024 GiB.

Sources share equally, including paused sources. Opening or closing a source redistributes the allowance.
Apply/OK resize existing caches immediately; shrinking evicts older frames. No viewer reopen is needed.
Cancel discards uncommitted edits. Select Auto to return to the default; Manual exposes a single total allowance in GiB.

Preferences are saved on normal application exit as `settings.playback.video_cache_total_mib`: `"auto"`
or a whole-MiB integer for Manual. This replaces the earlier per-source preference. Live transport buffers
and overlay caches are separate. Multiple lanes sharing one source also share that source's allowance.

## Measurements, 2026-09-19

Real Qt offline viewer widgets and production source workers, Linux, 30.50 GiB detected RAM, Qt offscreen,
640×360 viewer windows, no overlays. Each process played a synthetic moving H.264/yuv420p clip at
30 fps, 600 frames (20 seconds), with a 60-frame keyframe interval. Independent viewers used separate
source instances of the same file. Source decoding remained full resolution. RSS was sampled once
per second; these are the largest playback observations, not every transient allocation.

The controlled comparison below uses **Manual, 1 GiB total**; Auto depends on available RAM at startup.

| Independent viewers | Share per source | Combined cache reservation at EOF | Playback process RSS |
|---|---:|---:|---:|
| 1 × 1080p | 1024 MiB | 0.996 GiB | 1.54 GiB |
| 4 × 1080p | 256 MiB | 0.996 GiB | 2.20 GiB |
| 1 × 4K | 1024 MiB | 0.996 GiB | 2.04 GiB |
| 2 × 4K | 512 MiB | 0.973 GiB | 2.46 GiB |

Every viewer reached frame 599. Cache shares summed to 1 GiB in each case; reservations stayed within
them. The observed Qt event-pump gaps were below 20 ms. These offscreen observations do not certify
real desktop/GPU presentation or a long-run RSS plateau. Some runs overlapped checks, so timing
comparisons are not isolated performance benchmarks. RSS includes much more than cached pixels.

A live-resize check with two playing 4K viewers reduced the total allowance to 1 GiB: cache reservations
fell from 7.14 GiB to 0.97 GiB while playback continued. Closing one viewer reassigned the full allowance
to the remaining source; closing both removed all cache registrations.

An earlier 300-frame-per-source implementation reached 10.02 GiB playback RSS for four 1080p viewers;
two 4K viewers exceeded 16 GiB and were stopped early. Fixed-share comparisons of 256 and 512 MiB
showed the expected tradeoff: larger shares retained more seek history at higher memory cost, without
establishing a playback benefit for these clips.

With a 256 MiB total allowance and fresh application index caches, opening a single 20-second clip took
about 0.27 s at 1080p and 0.68 s at 4K. Reopening in a fresh process with the saved index took about
0.09 s for either clip. Filesystem pages were warm; these are not cold-disk or long-recording measurements.
At EOF, a single 1080p frame-550 read took about 8 ms at 256 MiB versus under 1 ms at 512 MiB, where
it remained cached. Those are source-read timings, not end-to-end GUI seek latency.

## Limits outside the cache

The allowance limits **video-cache reservations**, not total application RAM. Decoder reference
frames, intermediate frames collected during seeking, RGB conversion temporaries, display images and
allocator-retained pages are additional. Closing or shrinking caches releases their references, but the
allocator may keep resident pages for reuse. Auto does not monitor other applications or prevent swapping.

A separate 4K clip with a 300-frame keyframe interval illustrates this: a single viewer with a 256 MiB
allowance used about 0.98 GiB during playback, but a frame-550 seek took about 0.53 s and process RSS
subsequently reached **3.82 GiB**. Intermediate seek-frame retention remains outside this cache change.
Do not interpret the table or cache allowance as a guaranteed total-RAM requirement.

The diagnostics Sources tab reports cached frame count and reserved MiB / current source share. JSON
export schema 4 exposes `reserved_bytes` and `budget_bytes`; these are cache accounting, not measured RSS.

For reproducible media, use FFmpeg `testsrc2` at the listed dimensions and 30 fps, 20 seconds,
`libx264 -preset ultrafast -crf 28 -g 60 -pix_fmt yuv420p -threads 2`. For the long-GOP case use
`-g 300 -sc_threshold 0`. Measure fresh processes, record indexing separately from cached opens,
play to EOF, and probe retained and evicted frames. Also change the allowance during playback and
open/close sources to verify redistribution and cleanup.
