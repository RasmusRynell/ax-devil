# MOT Challenge sequences and overlays

The [MOT Challenge](https://motchallenge.net/) pedestrian-tracking benchmarks play as they are: the `mot_challenge`
playlist resolver reads the dataset's folders and the `MOT_FILE` decoder reads `det.txt` and `gt.txt`. No video
conversion is needed.

To try it without downloading anything, use the small generated dataset in `fixtures/datasets/mot`.

## Download

Keep the dataset outside the repository:

```bash
mkdir -p ~/datasets/mot
curl --fail --location https://motchallenge.net/data/MOT17.zip --output ~/datasets/mot/MOT17.zip
unzip -q ~/datasets/mot/MOT17.zip -d ~/datasets/mot
```

For MOT20, replace `MOT17` with `MOT20`. `MOT17Labels.zip` holds the annotations without frames and is not enough.

## Open it

In the app, choose **File > Add Playlist** (Ctrl+Shift+N), pick **MOT Challenge** and the dataset folder, **Scan** to
list its sequences (all start selected), untick any you do not want and **Load**.

From the command line, after `make build`, either command opens a new Untitled workspace with the playlist:

```bash
pnpm --dir desktop exec electron . playlist mot_challenge ~/datasets/mot/MOT17 [--sequences MOT17-04-DPM,MOT17-04-FRCNN]
target/release/ax-devil playlist mot_challenge ~/datasets/mot/MOT17    # serves http://127.0.0.1:7330 for a browser
```

`make desktop` and `make web` take no arguments; add the playlist from the dialog there. A folder that cannot be read
does not stop the app: the playlist is listed as failed, with the reason.

The folder may be the dataset root, its `train` or `test` folder, or any folder of sequence folders that each hold a
`seqinfo.ini`. A single sequence folder is not accepted; point at a folder that contains it. `--sequences` takes
sequence folder names, so a detector variant left out loses its lane.

## Lanes

MOT17 ships every sequence once per public detector (`MOT17-04-DPM`, `-FRCNN`, `-SDP`). The variants form one entry
with one lane per overlay on the shared frames: **DPM**, **FRCNN** and **SDP** detections and one **GT** lane (ground
truth is identical across variants). A sequence without a detector suffix, such as MOT20's, is one entry with a
**DET** lane and, in `train`, a **GT** lane. The `test` split has no ground truth. Each lane's settings (the tag's
sliders button) pick the catalog and the classes to show; the **Tracking** catalog colours each box by track id.

## What is read

- Frames are matched by number: file frame 1 is video frame 0. Boxes are scaled by the frame size in `seqinfo.ini`.
- Public detections have no track ids, so the Tracking catalog is meaningful on ground truth only. A detection's score is kept as it
  is; ground truth has none.
- Ground-truth classes are the benchmark's 1 to 13; every row is shown, including static people, distractors and
  rows with `mark` 0 that the benchmark ignores when scoring. Hide them with the class toggles. `mark` and
  visibility are kept as the `gt_mark` and `visibility_ratio` attributes. A row that does not parse is skipped and
  the lane warns.

## Limits

- Frames are numbered from `000001` in the folder `imDir` names, `img1` and `.jpg` by default. Both must be plain
  names; a sequence whose `imDir` or `imExt` is not fails to load.
- Only the built-in catalogs exist for now, hidden entries and lanes are not saved, and a lane's choices last while its
  entry is open. What the old app did that this one does not yet is in [parity](../parity.md) (rows IN-\*, RC-02 and
  OF-\*).

## License and citations

MOT Challenge sequences and annotations are licensed under
[CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/): non-commercial use, with attribution, and
derived material shared under the same license. Keep the dataset's license with local copies and credit it next to
any published video or GIF. The authors request these citations:

- A. Milan, L. Leal-Taixé, I. Reid, S. Roth, and K. Schindler, "MOT16: A Benchmark for Multi-Object Tracking,"
  arXiv:1603.00831, 2016. (MOT16 and MOT17)
- P. Dendorfer, H. Rezatofighi, A. Milan, J. Shi, D. Cremers, I. Reid, S. Roth, K. Schindler, and L. Leal-Taixé,
  "MOT20: A benchmark for multi object tracking in crowded scenes," arXiv:2003.09003, 2020. (MOT20)
