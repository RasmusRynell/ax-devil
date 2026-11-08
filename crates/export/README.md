# export

An export job: decode a frame range, work out which sample each lane draws on every frame, hand exactly that to a
[`Renderer`] for pixels, and encode them into a temporary sibling that replaces the destination only on success. It
knows no window, socket or Electron; the render host implements `Renderer`, and the root crate turns the wire request
into an `ExportSpec`.

- The cells come from `overlays::cell`, the function that builds the viewer's Frame Maps, so a file and the screen
  agree on what a frame shows.
- The renderer page holds the items named by the previous job, so each job carries only the samples the one before did
  not name. At most `IN_FLIGHT` jobs wait for pixels.
- Frame times are the source's (`FrameIndex`) and a frame lasts what the decoder reported for it, else the index's delay to the next frame, so a variable frame rate survives.
- A failure or cancel removes the temporary file and leaves the destination alone.

## Measured

`make bench-export`: one lane of the Standard catalog, Balanced MP4, a grained generated source, frames per second
after the first (which carries Electron's 0.6 to 0.8 s start). Electron 42.11.18, 16 threads, RTX 5080, on the private
display-less run of `scripts/isolated`, where the hidden page reached the card through Vulkan. Run on 2026-11-02;
treat them as an order of magnitude, nothing asserts them.

| Source | Boxes per frame | SwiftShader | GPU |
|--------|----------------:|------------:|----:|
| 1080p | 100 | 29 fps | 366 fps |
| 1080p | 1000 | 17 fps | 429 fps |
| 4K | 100 | 7.5 fps | 65 fps |

