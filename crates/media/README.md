# media

Video files to frames, for every consumer of decoded video (playback today; export and thumbnails later). It knows
FFmpeg and file formats, not windows, threads or the wire.

- The index is built from packets, not by decoding, because the scan then runs at I/O speed: in the spike, 1.1 s against
  36 s for 10 minutes of 1080p30, with identical results on every fixture. The reference decoded the whole video.
- A cached index is checked by a fingerprint, not a content hash, so a multi-gigabyte video reopens at once; the cost is
  in `fingerprint.rs`. The data-pipeline digest rule in [invariants](../../docs/domain/invariants.md#data-pipeline)
  covers Scene artifacts, not this index.
- Any change to the index file layout bumps `VERSION` in `index_file.rs`; a file of another version is rebuilt.
- `unsafe` stays in `ffi.rs`, each block with a `SAFETY` comment; no lint enforces it, so reviews do.
