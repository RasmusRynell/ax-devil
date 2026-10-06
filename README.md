<p align="center">
  <img src="https://raw.githubusercontent.com/RasmusRynell/ax-devil/main/src/ax_devil/resources/ax-devil-banner.png" alt="ax-devil — Development Utilities for Axis Devices" width="620" />
</p>

# Inspect video and analytics together

**ax-devil** is a Linux desktop app for inspecting video with analytics overlays.
Open your recordings and metadata or connect to a live Axis camera. Follow objects, compare results,
and add custom data formats through plugins.

[Get started](#get-started) · [Open your data](#open-your-data) · [Inspect and compare](#inspect-and-compare) · [Plugins](#extend-it-with-plugins) · [Overlay appearance](#overlay-appearance)

<p align="center">
  <img src="https://raw.githubusercontent.com/RasmusRynell/ax-devil/main/docs/media/demo-catalogs.webp" alt="ax-devil playing a highway recording with a car selected in the inspector while the overlays switch between render catalogs" width="100%" />
</p>

> **Under active development.** Interfaces and configuration may change.

## Get started

Install **Python 3.10+**, **uv**, and the [native dependencies](https://github.com/RasmusRynell/ax-devil/blob/main/docs/installation.md#ubuntu-2404-prerequisites),
then run from source:

```bash
git clone https://github.com/RasmusRynell/ax-devil.git
cd ax-devil
uv sync --locked
uv run ax-devil
```

Add data through **File**, or use the commands below from the repository directory. To use ax-devil without
changing it, install it from PyPI with `uv tool install ax-devil` and drop `uv run` from the commands.
[Installation and troubleshooting →](https://github.com/RasmusRynell/ax-devil/blob/main/docs/installation.md)

## Open your data

Open a recording, optionally with CVAT annotations:

```bash
uv run ax-devil local --video recording.mp4

uv run ax-devil local --video recording.mp4 \
  --overlay annotations.xml --handler-type CVAT
```

Built-in decoders include CVAT, MOT, UVG-VCM, Axis ADF, and ONVIF XML. List file decoders with
`uv run ax-devil list-handlers`.

Set camera credentials in **Settings → Stream defaults**, then connect:

```bash
uv run ax-devil live --host 192.168.1.100
```

Live analytics can arrive over RTSP, MQTT, or Axis DataHub WebSocket. For embedded ONVIF analytics:

```bash
uv run ax-devil live --host 192.168.1.100 --overlay rtsp --handler-type ONVIF_XML
```

DataHub disables certificate verification; see [connection options and limitations](https://github.com/RasmusRynell/ax-devil/blob/main/docs/usage.md#live-cameras).

## Inspect and compare

Click the video: **Space** plays or pauses, **,** / **.** step one frame, and **Left** / **Right** step ten.

Double-click the right-edge handle to open the inspector. Filter by object type, or select an object
to inspect its attributes and history.

Arrange viewers with drag-to-split layouts. Playlists group recordings and comparison lanes into a review sequence.
Comparison lanes play several overlays on the same video in sync:

<p align="center">
  <img src="https://raw.githubusercontent.com/RasmusRynell/ax-devil/main/docs/media/demo-compare.webp" alt="Two comparison lanes on the same highway recording: a simulated detector run (DET) beside ground truth (GT)" width="100%" />
</p>

To pair a folder of videos with CVAT annotations:

```bash
uv run ax-devil playlist folder_pair /path/to/videos /path/to/annotations --handler-type CVAT
```

**Export video with overlays** saves the result with source frame timing and no audio.

## Extend it with plugins

| Plugin | What you can build |
|---|---|
| **Decoder** | Read custom files or live messages into objects, geometry, attributes, and events. |
| **Playlist resolver** | Turn folders, manifests, or experiment results into recordings with matching overlays and comparison lanes. |

For example, decode your model's JSON output and pair two model runs with the same recordings.
Decoded data uses the viewer's existing filtering, inspection, and rendering tools.

Install a plugin and restart:

```bash
uv run ax-devil plugins install /path/to/your-plugin
uv run ax-devil
```

Packages and wheels are also supported. Plugins run with your permissions.
[Plugin implementation, packaging, and upgrades →](https://github.com/RasmusRynell/ax-devil/blob/main/docs/plugins.md)

## Overlay appearance

Each viewer has a **render catalog** controlling boxes, labels, scores, and attribute displays.

| Style | Appearance |
|---|---|
| **Standard** | Colored outlines, object IDs, and scores. |
| **Minimal** | Thin boxes; details on hover. |
| **Chunky** | Thick outlines and large labels for small previews. |
| **Glass** | Rounded boxes, dark labels, score meters, and attribute tags. |
| **Tracking** | Thin high-contrast boxes and small IDs, colored per object ID, for spotting ID switches. |
| **Classic** | The original look: IDs above boxes, class names below, confidence bars, and movement symbols. |

Preview styles through **View → Render Catalogs** (`Ctrl+R`) or:

```bash
uv run ax-devil catalog
```

1. Select a starting catalog and choose **New copy…**.
2. Edit its JSON, or ask an agent:
   “Make people orange and vehicles blue,” “Enlarge object IDs and hide confidence labels,” or
   “Show a badge when a person's `carries_bag` attribute is true.”
3. Choose **Apply to all** or **Use as default** in the viewer, or select the copy in the inspector's **Catalog**
   control. Edits appear on save.

Attribute badges require data from your decoder. [Catalog format and drawing tools →](https://github.com/RasmusRynell/ax-devil/blob/main/docs/architecture/draw-system.md#catalog-language)

## Contributing

Built with Python and PySide6.

```bash
make check
QT_QPA_PLATFORM=offscreen make test
```

[Architecture](https://github.com/RasmusRynell/ax-devil/blob/main/docs/architecture/overview.md) · [Module map](https://github.com/RasmusRynell/ax-devil/blob/main/docs/architecture/module-map.md) ·
[Testing](https://github.com/RasmusRynell/ax-devil/blob/main/docs/runbooks/testing.md) · [Development setup](https://github.com/RasmusRynell/ax-devil/blob/main/docs/installation.md#developing) ·
[Issues](https://github.com/RasmusRynell/ax-devil/issues)

Configuration and logs may contain credentials. [Redact diagnostics before sharing.](https://github.com/RasmusRynell/ax-devil/blob/main/docs/settings.md#configuration-and-local-data)

## License

[MIT](https://github.com/RasmusRynell/ax-devil/blob/main/LICENSE).

Demo footage and ground truth: HighwayDrive from [UVG-VCM](https://tie-ultravideo.rd.tuni.fi/UVG-VCM/index.html),
Ultra Video Group, Tampere University, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The DET lane is
simulated from that ground truth.
