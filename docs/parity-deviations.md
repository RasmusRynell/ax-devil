# Parity deviations from the reference

Where [parity](parity.md) rows differ from the reference. The offline decisions taken with the owner are in
[14](rebuild/14-build-order.md#decisions-taken-for-offline-video). The rest:

| Row | Deviation | Reason |
|-----|-----------|--------|
| AC-04 | The playlists of one item share one viewer, so two cannot be opened side by side; the reference opened each in its own | A viewer per playlist needs the split viewer area (WS-13) |
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
| IN-03 | Hover and click pick over the lane's filtered rows, not what was drawn, so an object that draws nothing can still be hovered and selected; the reference picked from the drawing | D36: picking is frontend only, with no drawing pass to ask |
| RC-03 | Copy is an inline Name field in the lane menu's Look page, not a dialog | The lane menu is where a catalog is chosen |
| RC-03 | A copy records `origin` as a plain string with no base hash, so there is no "modified" note | A copy is the source text under a new name |
| RC-03 | Copy refuses a source with a duplicate top-level key; the file name is lowercased, not casefolded | The compiler would silently keep the last key |
| RC-03 | A file name that cannot be an id is listed invalid and cannot be deleted; at most 256 files are listed | A catalog id is the file name as text; the folder poll is bounded |
| RC-04 | `catalog render` is dropped; `check` still exercises the catalog on the Overview example item without drawing a picture | Owner decision: "remove all of it with the preview render catalog stuff", after RC-03 dropped the preview sheets |
| RC-04 | `catalog use` writes the settings file. A running app keeps the settings it loaded: the choice applies at its next start, and any settings save in the running app first overwrites it | The default is a Personal setting that only the settings service writes in a running app |
| RC-02 | The default catalog is a Personal setting, so it does not follow `storage.catalog_dir` | A Personal setting travels with the person, not with a folder |
| VPC-016, VPC-017, VPC-A003, DRW-A001 | The side panel has no hover grip, double-click toggle or 400 ms width animation: only its command, Close button and divider open, close and resize it. The width follows the reference's rule and the video shrinks beside it | Owner decision: buttons and the divider are discoverable, a grip is a hidden hover target |
| VPC-009 | Wheel zoom scales with the wheel delta, at most one 1.15x step per event; the reference zoomed a full step per event by its sign, so a trackpad jumped to the limit and a horizontal scroll zoomed out. A mouse notch is identical | A trackpad sends many small deltas |
| VPC-010 | Panning starts after 3 CSS px; the reference panned from the first pixel, so a zoomed click with any movement did not select. A double click resets the view (new; the reference reset only through the menu or Ctrl+0) | A click that wobbles still selects |
| DRW-004 | Catalog v4 has no open polyline mark; the reference had one | No built-in catalog or decoder used it |
| DRW-006 | Overlay text is laid out per glyph, without pair kerning or ligatures (joining scripts rasterize whole lines); the reference shaped it through Qt. Class names differ by about 0 to 1 px, digits not at all | The canvas lays text out itself |
