# MOT Challenge sequences and overlays

Use the [MOT Challenge](https://motchallenge.net/) pedestrian-tracking benchmarks from the Computer Vision Group,
Technical University of Munich. The built-in `mot_challenge` playlist resolver reads the dataset's folder layout
directly, and the `MOT_FILE` decoder reads its `det.txt` and `gt.txt` files. No video conversion is needed: each
sequence plays from its JPEG frames.

## Prepare a playlist

From the repository directory, download and unpack a benchmark. MOT17 is about 5.5 GB; MOT20 is about 4.7 GB.
`curl` and `unzip` are only needed for this step:

```bash
mkdir -p downloads/mot
curl --fail --location https://motchallenge.net/data/MOT17.zip --output downloads/mot/MOT17.zip
unzip -q downloads/mot/MOT17.zip -d downloads/mot
```

For MOT20, replace `MOT17` with `MOT20`. `MOT17Labels.zip` contains annotations without frames and is not enough
on its own.

Open the playlist:

```bash
uv run ax-devil playlist mot_challenge downloads/mot/MOT17
```

Alternatively, use **File → Add playlist**, choose **MOT Challenge**, and select the dataset folder.

The resolver accepts the dataset root, its `train` or `test` folder, or any folder of sequence folders that each hold a
`seqinfo.ini`. To open one sequence, point it at a folder containing only that sequence's variants.
`seqinfo.ini` supplies the frame rate, frame size, length, image folder and extension.

## Comparison lanes

MOT17 ships every sequence three times, once per public detector: `MOT17-04-DPM`, `MOT17-04-FRCNN`,
`MOT17-04-SDP`. The resolver groups these into one playlist entry with one lane per overlay on the shared frames:
the detections of each variant, labeled **DPM**, **FRCNN** and **SDP**, plus one **GT** lane. Ground truth is
identical across the variants, so it is shown once. A training sequence opens as four synchronized lanes; a test
sequence has no ground truth and opens as three.

Sequences without a detector suffix, such as MOT20's, form one entry each, with a **DET** lane and, in the
training split, a **GT** lane. A sequence with neither file opens as plain video.

## Decoder behavior

- Rows with 7 or 10 columns are detections; rows with 9 columns are ground truth
  (`frame, id, left, top, width, height, mark, class, visibility`). Other rows are skipped and logged at debug
  level; loading continues.
- Source frame 1 maps to video frame 0. Frames are matched by number; no timestamps are invented.
- Pixel boxes are normalized using the frame size from `seqinfo.ini`. Boxes extending past the image are kept.
- Track IDs become entity IDs. Public detections use ID `-1`; each one is shown as a separate frame-local
  `untracked:…` object, so detection lanes have no object history.
- Detection scores between 0 and 1 become observation confidence. Scores outside that range, such as DPM's,
  leave confidence absent. The original score is always kept in the `raw_score` attribute.
- Ground-truth classes use the benchmark's 1–13 list (person, person on vehicle, car, … crowd). Every row is
  shown, including static people, distractors, occluders and rows with `mark` 0, which the benchmark ignores when
  scoring; use the class filter to hide them. `mark` is kept as the `gt_mark` attribute and visibility as
  `visibility_ratio`. Ground-truth labels have classification score 1.0.
- Decoded files are cached; the cache invalidates when the source changes.

## Attribution and license

MOT Challenge sequences and annotations are licensed under
[CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/): non-commercial use only, with
attribution, and derived material shared under the same license. The application's Python code remains MIT
licensed. `downloads/` is ignored by Git; keep the dataset's license terms with local copies and credit the
dataset next to any published video or GIF.

The authors request these citations:

A. Milan, L. Leal-Taixé, I. Reid, S. Roth, and K. Schindler,
“MOT16: A Benchmark for Multi-Object Tracking,” arXiv:1603.00831, 2016. (MOT16 and MOT17)

P. Dendorfer, H. Rezatofighi, A. Milan, J. Shi, D. Cremers, I. Reid, S. Roth, K. Schindler, and
L. Leal-Taixé, “MOT20: A benchmark for multi object tracking in crowded scenes,” arXiv:2003.09003, 2020. (MOT20)
