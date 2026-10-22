# Application demo recorder

Record the whole ax-devil window on a private Xvfb display, then export MP4 and looping WebP.
The app opens real videos and overlays. Capture seeks by source timestamp and waits for the requested
frame before reading back Qt Quick, so output timing does not depend on the machine's playback speed.
Audio is not recorded. This is an offline demo tool, not a real-time screen recorder or performance benchmark.

Run from the repository root with the development environment installed (`uv sync`). On Linux,
install `ffmpeg`, `Xvfb`, `xvfb-run` and `xauth`. Package names are `ffmpeg xorg-server-xvfb xorg-xauth`
on Arch, or `ffmpeg xvfb xauth` on Debian/Ubuntu. No extra Python dependencies are needed.

## Capture

```bash
uv run python -m tools.demo_recorder capture \
  --video downloads/uvg-vcm/videos/HighwayDrive.mp4 \
  --overlay downloads/uvg-vcm/overlays/HighwayDrive.json --handler UVG_VCM \
  --output downloads/demos/playback
```

Default: 300 output frames at 30 fps, 1600×900, Glass overlays, dark theme, a media-tools panel
opening after one second, and the Midnight treatment (blurred background, rounded corners and shadow).
Omit `--overlay` and `--handler` for plain video. The tool loads plugins available in the development
interpreter; it does not use plugins installed into the user's separate application environment.

- `--frames 60 --fps 20`: make a three-second take.
- `--start 2`: begin two seconds into the source; variable frame rates use the decoded timeline.
- `--width 1600 --height 800`: choose the app and output dimensions (even numbers).
- `--inspector-at 0`: open the panel from the first frame; `-1` disables it.
- `--select-object 1`: open this object's page in the inspector (default: the first listed).
- `--catalogs minimal`: choose a built-in catalog for playback or comparison.
- `--raw-only`: capture without producing styled exports.
- `--style plain`: export the app without decoration.

A take that extends beyond the final source timestamp fails instead of silently holding its last frame.
Choose a window large enough for the app's minimum size. Capture refuses existing output directories.

## README recipes

These recreate the original workflows using the current application. They do not promise pixel-identical
copies of the old README files, and they never replace or publish those files.

Catalog wipe, with a selected object in the inspector:

```bash
uv run python -m tools.demo_recorder capture \
  --video downloads/uvg-vcm/videos/HighwayDrive.mp4 \
  --overlay downloads/uvg-vcm/overlays/HighwayDrive.json --handler UVG_VCM \
  --recipe catalogs --inspector-at 0 --select-object 1 --width 1600 --height 650 \
  --output downloads/demos/catalogs
```

The take divides equally among Glass, Standard, Tracking, Chunky, Classic and Minimal. Each transition
reveals the next catalog over the same source frame within the video viewport. Supply `--catalogs glass minimal`
to choose a shorter sequence. The inspector belongs to the current catalog; the wipe is a composited video effect.

Comparison lanes on one video, with deliberately imperfect, seeded detections derived from UVG-VCM ground truth:

```bash
uv run python -m tools.demo_recorder capture \
  --video downloads/uvg-vcm/videos/HighwayDrive.mp4 \
  --overlay /tmp/gt/GT.json --handler UVG_VCM \
  --recipe compare --simulate-det --catalogs tracking --width 1600 --height 430 \
  --output downloads/demos/compare
```

A lane is named after its overlay file, so copy the annotations to `/tmp/gt/GT.json` first to name the lane **GT**.
The generated file is `DET (simulated).json` in the take folder, separate from the original annotations, and the lane is named
**DET (simulated)**. It drops some boxes and perturbs their positions; it is not a model evaluation.
For real comparison data, replace `--simulate-det` with `--compare-overlay PATH`. Both overlays use `--handler`.
The comparison recipe leaves both media-tools panels closed.

## Restyle without recapturing

```bash
uv run python -m tools.demo_recorder render \
  --input downloads/demos/catalogs/raw.mp4 \
  --style plain --output downloads/demos/catalogs-plain
```

Each completed capture contains:

| File | Purpose |
|---|---|
| `raw.mp4` | Lossless H.264 capture, at the requested output frame rate, for restyling |
| `take.json` | Capture arguments and exact source-frame indices |
| `capture.log` | Application and capture diagnostics |
| `xvfb.log` | Private display startup diagnostics |
| `raw-preview.png` | First frame before styling |
| `demo.mp4` | Styled H.264 video |
| `demo.webp` | Looping animation, 15 fps and at most 1280 pixels wide |
| `preview.png` | Styled frame near two seconds, or the first frame for a shorter take |

Restyling writes the last three files only. Failed encoding leaves `.partial.mp4` instead of advertising a
finished take. Raw H.264 uses lossless codec settings but converts to YUV 4:2:0; it is not a pixel-exact RGB archive.
Generated outputs belong under ignored `downloads/` or `/tmp`. Credit source footage when sharing it; the
[README](../../README.md) credits the UVG-VCM footage used by these examples.

## Isolation and maintenance

The launcher gives each take a temporary HOME and XDG directories, removes inherited Qt/app/display overrides,
and allocates its own X server with `xvfb-run -a`. It owns one process group and cleans it up on completion or
interruption. It never uses the desktop display or global process-name kills. The temporary profile and caches
are removed after the take. On failure, read `capture.log` and `xvfb.log`; restricted sandboxes may need permission to create
Xvfb's local socket. Rendering alone uses Qt offscreen.

The tool has three parts: `__main__.py` owns CLI validation and isolation; `capture.py` owns application choreography
and catalog composition; `render.py` styles saved footage. Application-private access is concentrated in `capture.py`
for Workspace readiness and synchronous Quick readback. Changing those internals may require updating the tool.
There is no recording code in the installed application. This rebuild intentionally omits the old experimental
cursor animations, camera zooms, captions, GIFs and ten-style batch generator.

Run `make check` and `QT_QPA_PLATFORM=offscreen make test`. For a native smoke check, generate small input and
record it (the command creates its own isolated display):

```bash
ffmpeg -f lavfi -i testsrc2=size=640x360:rate=30 -t 3 -c:v libx264 -pix_fmt yuv420p /tmp/demo-input.mp4
uv run python -m tools.demo_recorder capture --video /tmp/demo-input.mp4 \
  --frames 45 --width 1280 --height 800 --output /tmp/demo-check
```

Inspect `preview.png` and probe `raw.mp4` to confirm the requested dimensions, frame count and duration.
The tests cover variable source timing, isolation, synthetic annotations and compositing. Native capture requires
the smoke check above; see the [GUI verification policy](../../docs/runbooks/testing.md#gui-verification).
