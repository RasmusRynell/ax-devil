# scene-store

Decodes an overlay file once, keeps every Scene in a private artifact on disk, and serves Scenes by sample index.
Memory per open track is the sample keys and the record index (about 12 bytes per sample) plus a byte-bounded
cache of recently used Scenes (`SCENE_CACHE_BYTES`, 32 MiB). The Scenes themselves stay on disk, so a file's size
does not change what the app holds (the reference app held about 690 MB for a 50 MB CVAT file). `Track::item` adds a
second cache of encoded Sample Items (16 MiB per track), shared by every viewer of the track.

## Artifact

`<cache>/scenes/<sha256 of TrackKey>.axscene`, one per (file, handler, options, frame size), created private (0600,
folder 0700), written to a temporary sibling and renamed into place. Without a cache folder the artifact is an unnamed
temporary file for the life of the track.

```
header   "AXSC" | STORE_FORMAT_VERSION u32 LE
records  one postcard Scene each (scene::serial::encode), back to back; record r is sample r
index    postcard: sample keys (frames or µs, ascending, unique), record boundaries, CRC-32 per record
summary  postcard: coverage, decoder warnings, filter policy, file metadata
identity postcard: handler, artifact version, Scene version, canonical options, frame size,
         decoder code hash or none (converters), source stat (size, mtime, ctime where the platform has one), source SHA-256 or none
history  CRC-32 then postcard: the file history (see File history), or a mark that it was too large
footer   index, summary, identity and history offsets u64 LE | CRC-32 of index..history u32 LE | "AXSE"
```

The tail follows the records because the digest is only known after the build: a source that changed while it was
read is stored with no digest, and an artifact without a digest is never reused. Records carry their own CRC-32
checked on every read; a damaged record is an error for that Scene only and unlinks the artifact (readers holding it
keep it, the registry stops sharing the track), so the next open rebuilds it. Any damage to the header, footer or tail
makes the artifact unusable and rebuilt, as does a section larger than any artifact writes (index 256 MiB, summary
64 MiB, identity 1 MiB, history 257 MiB, record 256 MiB), so a damaged file cannot ask for a large allocation.

Reuse (invariants, Data Pipeline): every identity component must match. A matching stat on a platform with a change
time proves the source unchanged and skips hashing; otherwise the source is hashed, and a matching digest with a
different stat republishes the artifact with the new stat (atomic copy) instead of decoding. The stat is taken from
the open source handle before it is read, and the Scenes are read through the handle that wrote or validated the
artifact.

## Ingest

Samples are keyed by the Scene's start time, and a file uses one clock (frame numbers or timestamps); mixing them
fails the decode. A decode runs on its own thread (`scene-ingest-<handler>`) and feeds a `SceneSink`; the thread
holds no `Arc<Track>`, so dropping the last track stops it.

- **Groups.** Scenes with one start key are one group: `Merge` joins them (entities in order of first appearance, an
  id seen again adds its observations and images, relations, events and debug fields are appended, the slice ends at
  the later end), `Replace` drops what the key held and warns "N samples were replaced". The open group is capped
  at 256 MiB.
- **Watermark.** While keys ascend, a larger key closes the open group into a record. A decoder that gave a header
  (filter, coverage, metadata) is published every 250 ms at most: `TrackSnapshot::decided` is `Below(open key)`, every
  frame before it is final, and the track is returned to the opener at the first publication. Without a header the
  track is returned at the end.
- **Generation.** The first key below the open one freezes the watermark. Every later Scene is written as a fragment
  record and the end sorts the fragments (`sort.rs`: 64 MiB of 16-byte entries in memory, else sorted runs in a
  temporary file beside the artifact), joins them per key into a new artifact and publishes it with `generation + 1`,
  only in the publication that ends the ingest. A snapshot holds its numbering: read a sample index with
  `Track::scene_in` / `item_in` and the snapshot it came from, never with `Track::scene` across an `await` or thread
  hop. Listeners (`Track::on_change`) hear every publication.
- **End.** A complete decode writes the tail and renames the artifact into place before `Complete` is published. A
  failure after a sample, from a converter or a decoder that gave a header, leaves the rows read, `Incomplete(why)` and
  no stored artifact; any other failure fails the open.

## File history

`history.rs` builds, in the same pass that writes the records, what the Objects list needs without decoding the file
again: per entity its id, every class it was recorded with (first-appearance order), the kinds of event it is in and
its runs of consecutive samples; per event its sample, label and entities; and per entity a file class mask, judged
by the policy's `ClassRule`s over all recorded classes (the frame mask judges only the latest observation, so a car
that became a bus matches both). The mask is computed when the policy is known, at the end, so a decoder without a
header gets it too. When the ingest froze, the history is built in the merge pass, over the final numbering.

It is held only while a query holds it: `Track::history` reads the artifact section (own checksum, read without the
rest of the tail) when nobody holds it. The estimate while building is capped at `HISTORY_BYTES` (256 MiB); beyond it
the section says so and `HistoryUnavailable::TooLarge` is the answer. An incomplete ingest stores no artifact, so its
history stays in memory with the track. While the ingest runs there is no history (`Reading`).

`query.rs` evaluates a parsed search over it (toggles, search, sort, pages; `>` `<` `>=` `<=` never match a text
value, as in the reference app) and `placement.rs` puts runs on a video's frames by asking `overlay_sync::select` once
per frame.
