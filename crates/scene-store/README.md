# scene-store

Decodes an overlay file once, keeps every Scene in a private artifact on disk, and serves Scenes by sample index.
Memory per open track is the sorted sample keys and the record index (about 12 bytes per sample) plus a byte-bounded
cache of recently used Scenes (`SCENE_CACHE_BYTES`, 32 MiB). The Scenes themselves stay on disk, so a file's size
does not change what the app holds (the reference app held about 690 MB for a 50 MB CVAT file). `Track::item` adds a
second cache of encoded Sample Items (16 MiB per track), shared by every viewer of the track.

## Artifact

`<cache>/scenes/<sha256 of TrackKey>.axscene`, one per (file, handler, options, frame size), created private (0600,
folder 0700), written to a temporary sibling and renamed into place. Without a cache folder the artifact is an unnamed
temporary file for the life of the track.

```
header   "AXSC" | STORE_FORMAT_VERSION u32 LE
records  one postcard Scene each (scene::serial::encode), back to back, in emission order
index    postcard: sample keys (frames or µs, ascending, unique), record boundaries, CRC-32 per record,
         sample -> record order (empty when emission order already is key order)
summary  postcard: coverage, decoder warnings, filter policy, file metadata
identity postcard: handler, artifact version, Scene version, canonical options, frame size,
         source stat (size, mtime, ctime where the platform has one), source SHA-256 or none
footer   index, summary and identity offsets u64 LE | CRC-32 of index..identity u32 LE | "AXSE"
```

The tail follows the records because the digest is only known after the build: a source that changed while it was
read is stored with no digest, and an artifact without a digest is never reused. Records carry their own CRC-32
checked on every read; a damaged record is an error for that Scene only and unlinks the artifact (readers holding it
keep it, the registry stops sharing the track), so the next open rebuilds it. Any damage to the header, footer or tail
makes the artifact unusable and rebuilt, as does a section larger than any artifact writes (index 256 MiB, summary
64 MiB, identity 1 MiB, record 256 MiB), so a damaged file cannot ask for a large allocation.

Reuse (invariants, Data Pipeline): every identity component must match. A matching stat on a platform with a change
time proves the source unchanged and skips hashing; otherwise the source is hashed, and a matching digest with a
different stat republishes the artifact with the new stat (atomic copy) instead of decoding. The stat is taken from
the open source handle before it is read, and the Scenes are read through the handle that wrote or validated the
artifact.

Samples are keyed by the Scene's start time, and a file uses one clock (frame numbers or timestamps); mixing them
fails the decode. Time-keyed decoders may emit out of order: records stay in emission order, the index sorts them, and
of records sharing a key the last wins, with a warning on the track.
