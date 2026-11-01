# media

Video files and image sequences to frames, for every consumer of decoded video (playback today; export and
thumbnails later). It knows FFmpeg and file formats, not windows, threads or the wire.

- The index is built from packets, not by decoding, because the scan then runs at I/O speed: in the spike, 1.1 s against
  36 s for 10 minutes of 1080p30, with identical results on every fixture. The reference decoded the whole video.
- A cached index is checked by a fingerprint, not a content hash, so a multi-gigabyte video reopens at once; the cost is
  in `fingerprint.rs`. The data-pipeline digest rule in [invariants](../../docs/domain/invariants.md#data-pipeline)
  covers Scene artifacts, not this index.
- An image sequence is a video whose timeline is its frame rate times the index: nothing is scanned or cached, and
  every frame is a keyframe. FFmpeg's `image2` demuxer opens only the first image, for its codec and size. It ends the
  stream at the first missing file, so the decoder reads the image files itself: a missing image then costs one frame,
  and a seek is exact.
- Any change to the index file layout bumps `VERSION` in `index_file.rs`; a file of another version is rebuilt.
- `unsafe` stays in `ffi.rs`, each block with a `SAFETY` comment; no lint enforces it, so reviews do.
