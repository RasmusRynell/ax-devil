# Parity deviations from the reference

Where [parity](parity.md) rows differ from the reference. The offline decisions taken with the owner are in
[14](rebuild/14-build-order.md#decisions-taken-for-offline-video). The rest:

| Row | Deviation | Reason |
|-----|-----------|--------|
| OF-02 | Lanes share one video; the reference let a secondary lane have its own video, length and frame rate, aligned to the primary's frame index | D19: two recordings side by side are two viewers |
| RC-01 | Catalog schema v4 replaces v3: rules plus ordered layers (replacing the fixed order), label placement lists, one expression language, no `extends` and no `id` (a user catalog is its file). The reference's catalogs do not load | D25: the frontend evaluates catalogs per lane; D33: location is identity |
| OF-06 | The frame cache lives in `playback`, not `media` | Eviction follows playback windows; export, thumbnails and live skip it |
| OF-06 | Eviction takes the frame farthest from the playhead outside the protected windows; the reference used LRU | It suits scrubbing: after a jump, frames around the old position are the least wanted |
| OF-06 | Auto is never below 256 MiB; the reference had no floor | The setting's smallest value; a low-memory machine still gets a cache |
| OF-01 | The index is built by scanning packets; the reference decoded the whole video. The decode scan is the fallback | Spike: 1.1 s against 36 s for 10 minutes of 1080p30, same results |
| OF-01 | The index is valid by a stat fingerprint plus a digest of the first and last 64 KiB; the reference used the stat fingerprint alone | Hashing a huge video defeats instant reopen |
| OF-01 | Walking back through a group longer than the budget decodes a block of half the budget at a time | No keyframe group is decoded twice to keep one more frame |
| OF-01 | The client ends the session when the protocol version in `Opened` differs from its own | A stale window stops with a message, not misparsed frames |
| OF-01 | Playback commands sit in a View > Playback submenu; the keys, including Left and Right for 10 frames, are the reference's | There is no Playback menu |
| ST-01 | `storage.cache_dir` and `storage.catalog_dir` take no `$NAME` reference; [08](rebuild/08-settings-cli.md) asked for references in storage fields | Browse yields an absolute path; a reference would have to resolve before the first window opens |
| PL-02 | ADF beta lists the Bags filter before Faces; the reference listed Faces first | Beta and v1 share one filter list, in the v1 order |
| PL-02 | ADF v1 frame keeps a class it does not know, lowercased, including unknown, bag and face; the reference refused the record | Every ADF decoder reads a class through one function, which is the reference's beta and consolidated rule |
| PL-02 | ADF `Person` becomes `human` in beta frame and both consolidated formats; the reference kept `person` there (only v1 frame mapped it) | The same function maps the alias for every ADF decoder |
| PL-02 | `.json` overlays no longer auto-pick UVG-VCM: `local v.mp4 --overlay x.json` is refused naming the candidate decoders; choose one with `--handler-type` | A `.json` file fits several decoders, so the pick is the user's |
