# UVG-VCM videos and overlays

Use the [UVG-VCM dataset](https://tie-ultravideo.rd.tuni.fi/UVG-VCM/index.html) from the Ultra Video Group,
Tampere University. The built-in `UVG_VCM` file decoder reads its **Annotations JSON v1.0** directly.
No inference model or conversion to CVAT is needed.

## Prepare a playlist

The official page embeds ordinary MP4s alongside the RAW YUV downloads. Use the MP4s for convenient playback;
they are compressed previews, not the uncompressed benchmark originals.

From the repository directory, download the five videos and their annotations into separate folders with matching
file stems. `curl` is only needed for this download step:

```bash
mkdir -p downloads/uvg-vcm/videos downloads/uvg-vcm/overlays
for sequence in VolleyballGame FloorballGame JobFair TrafficLights HighwayDrive; do
  curl --fail --location \
    "https://tie-ultravideo.rd.tuni.fi/UVG-VCM/${sequence}/${sequence}.mp4" \
    --output "downloads/uvg-vcm/videos/${sequence}.mp4"
  curl --fail --location \
    "https://tie-ultravideo.rd.tuni.fi/UVG-VCM/${sequence}/${sequence}_annotations.json" \
    --output "downloads/uvg-vcm/overlays/${sequence}.json"
done
```

The dataset's current downloads are all 3840×2160 at 60 fps, with 420 frames for Floorball and 600 for the others.
Before substituting or transcoding videos, check their frame counts and preserve frame order. The decoder aligns
by frame number, so dropping frames or changing the start point misaligns overlays. Keep attribution outside
the two pairing folders; Folder Pair scans their files and matches stems.

Open the playlist:

```bash
uv run ax-devil playlist folder_pair \
  downloads/uvg-vcm/videos downloads/uvg-vcm/overlays --handler-type UVG_VCM
```

Alternatively, use **File → Add Playlist**, choose **Folder Pair**, select the two folders, and choose **UVG-VCM**
as the decoder. Entries are sorted by file name. For one sequence:

```bash
uv run ax-devil local --video downloads/uvg-vcm/videos/VolleyballGame.mp4 \
  --overlay downloads/uvg-vcm/overlays/VolleyballGame.json --handler-type UVG_VCM
```

The existing inspector provides class filters, source attributes, and object history. Render catalogs control
the visible labels and geometry, as they do for other decoders.

## Decoder behavior

- Only the reviewed object-detection/tracking JSON format declaring `"version": "1.0"` is supported.
  Frame keys must be consecutive strings `"1"` through the sequence length. An empty array is an explicit empty frame.
- Source frame 1 maps to video frame 0. No capture timestamp or frame rate is invented by the annotation decoder.
- Box coordinates are already normalized. They remain unchanged, including coordinates slightly outside the image.
- Class IDs use the dataset's contiguous 1–80 category list, rather than the sparse IDs in COCO JSON exports.
  Optional `class_name` must agree with the ID. Original records remain available in observation debug data.
- Normal track IDs become entity IDs without prefixes. If an ID occurs more than once in one frame, **every**
  colliding detection is displayed separately with a frame-local `ambiguous:…` ID. No persistent identity is guessed.
  The original `track_id` remains a classification attribute, and a warning reports affected source frames,
  including when loading a cached file. This occurs in four frames of the published Volleyball v1.0 annotations.
- `iscrowd` and `mask_color`, when present, are retained as attributes. The source has no detector-confidence field:
  observation confidence stays absent. The required classification score is 1.0 for the supplied ground-truth label;
  catalogs displaying classification scores may show that value.
- A JSON `polygon` replaces the box geometry with a normalized polygon. Highway Drive includes these polygons.
  Separate PNG segmentation masks, pose JSON, panoptic JSON, and oriented-detection formats are not decoded here.
- Malformed annotations fail loading with frame/field context; whole frames are not silently discarded.
  The existing derived Scene cache retains class filters and history, and invalidates when the source changes.

## Attribution and license

The footage and annotations are licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
The application's Python code remains MIT licensed. `downloads/` is ignored by Git; keep the dataset's attribution
with local copies and add it next to any published video or GIF:

> Demo footage and annotations derived from the [UVG-VCM dataset](https://tie-ultravideo.rd.tuni.fi/UVG-VCM/index.html)
> by the Ultra Video Group, Tampere University, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
> Visualization overlays were added by this project.

For unchanged official MP4s, credit video compression to the dataset authors; this project's overlays are drawn
at playback time. Describe the project's actual modifications when publishing a derived asset.

The authors request this citation:

T. Partanen, M. Anttila, R. Kortelahti, G. Gautier, A. Mercat, and J. Vanne,
“UVG-VCM: Benchmarking dataset for machine-oriented visual data compression,”
Proc. Int. Conf. Quality of Multimedia Experience (QoMEX), 2026.

Use only UVG-VCM assets. The older UVG dataset at `/dataset.html` has a different, non-commercial license.
