<p align="center">
  <img src="https://raw.githubusercontent.com/RasmusRynell/ax-devil/main/src/ax_devil/resources/ax-devil-banner.png" alt="ax-devil — Development Utilities for Axis Devices" width="100%" />
</p>

**ax-devil** is a Linux desktop app for viewing video together with analytics overlays. Open recordings with
their metadata or connect to a live Axis camera, inspect objects frame by frame, and compare results side by side.

<p align="center">
  <img src="https://raw.githubusercontent.com/RasmusRynell/ax-devil/main/docs/media/demo-catalogs.webp" alt="ax-devil playing a highway recording with a car selected in the inspector while the overlays switch between render catalogs" width="100%" />
</p>

> **Under active development.** Interfaces and configuration may change.

## Install

Requires Linux, **Python 3.10+**, and the [native dependencies](https://github.com/RasmusRynell/ax-devil/blob/main/docs/installation.md#ubuntu-2404-prerequisites).

```bash
uv tool install ax-devil
ax-devil
```

[Running from source, upgrades, and troubleshooting →](https://github.com/RasmusRynell/ax-devil/blob/main/docs/installation.md)

## AI-first

The easiest way to use, learn, and extend ax-devil is through a coding agent such as Claude Code or Codex. Clone the
repository, start the agent inside it, and say what you want:

```bash
git clone https://github.com/RasmusRynell/ax-devil.git
cd ax-devil
claude
```

- *"Connect to my camera at 192.168.1.50 with MQTT analytics, credentials from environment variables."*
- *"Write a decoder plugin for the JSON files in ~/runs/model_v3."*
- *"Make a playlist that pairs ~/data/videos with ~/data/labels and compares the two model runs."*
- *"Make people orange and vehicles blue, and hide confidence labels."*

The repository ships [agent instructions](https://github.com/RasmusRynell/ax-devil/blob/main/AGENTS.md) and
[skills](https://github.com/RasmusRynell/ax-devil/tree/main/.agents/skills) that teach the agent how ax-devil works.

## Open your data

Everything below is also available from the **File** menu.

```bash
# A recording
ax-devil local --video recording.mp4

# A recording with annotations
ax-devil local --video recording.mp4 --overlay annotations.xml --handler-type CVAT

# A live camera (set credentials in Settings → Stream defaults)
ax-devil live --host 192.168.1.100

# A live camera with ONVIF analytics from the RTSP stream
ax-devil live --host 192.168.1.100 --overlay rtsp --handler-type ONVIF_XML

# A folder of videos paired with a folder of annotations
ax-devil playlist folder_pair /path/to/videos /path/to/annotations --handler-type CVAT
```

**File → Save Workspace** saves what you opened as a `.ax-devil.workspace` file; `ax-devil open` reopens it, and a
plain `ax-devil` picks up where you left off. [Workspaces →](https://github.com/RasmusRynell/ax-devil/blob/main/docs/usage.md#workspaces)

Built-in formats: CVAT, MOT, UVG-VCM, Axis ADF, and ONVIF XML (`ax-devil list-handlers`). Live analytics can
arrive over RTSP, MQTT, or Axis DataHub WebSocket.
[Live connection options →](https://github.com/RasmusRynell/ax-devil/blob/main/docs/usage.md#live-cameras)

## Inspect and compare

| Key | Action |
|---|---|
| **Space** | Play / pause |
| **,** / **.** | Step one frame |
| **Left** / **Right** | Step ten frames |

- **Media tools panel** — `Ctrl+B`, or double-click the handle on the right edge of the video. Filter by object type,
  or select an object to see its attributes and history.
- **Layouts** — drag viewers to split the workspace.
- **Comparison lanes** — play several overlays on the same video in sync, for example a detector run against
  ground truth.
- **Playlists** — group recordings and comparison lanes into a review sequence.
- **Export** — save video with overlays drawn in (keeps source frame timing, no audio).

<p align="center">
  <img src="https://raw.githubusercontent.com/RasmusRynell/ax-devil/main/docs/media/demo-compare.webp" alt="Two comparison lanes on the same highway recording: a simulated detector run (DET) beside ground truth (GT)" width="100%" />
</p>

## Change how overlays look

Each viewer draws its overlays with a **render catalog**: a JSON file that styles boxes, labels, scores, and
attributes. Built-in catalogs:

| Catalog | Look |
|---|---|
| **Standard** | Colored outlines, object IDs, and scores |
| **Minimal** | Thin boxes, details on hover |
| **Chunky** | Thick outlines and large labels for small previews |
| **Glass** | Rounded boxes, dark labels, score meters, and attribute tags |
| **Tracking** | High-contrast boxes colored per object ID, for spotting ID switches |
| **Classic** | IDs above boxes, class names below, confidence bars, and movement symbols |

To make your own, open **View → Render Catalogs** (`Ctrl+R`) or run `ax-devil catalog`, pick a catalog, and choose
**New copy**. Edit the JSON yourself or ask a coding agent. Changes show up when you save. [Catalog format →](https://github.com/RasmusRynell/ax-devil/blob/main/.agents/skills/render-catalog/SKILL.md#how-a-catalog-is-built)

## Plugins

- **Decoder** — read your own files or live messages, such as a model's JSON output.
- **Playlist resolver** — turn folders, manifests, or experiment results into recordings with matching overlays.

Decoded data works with the existing filtering, inspection, and rendering.

```bash
ax-devil plugins install /path/to/your-plugin
```

Restart ax-devil to load it. Plugins run with your user permissions.
[Writing plugins →](https://github.com/RasmusRynell/ax-devil/blob/main/.agents/skills/write-plugin/SKILL.md) ·
[Installing and managing plugins →](https://github.com/RasmusRynell/ax-devil/blob/main/docs/plugins.md)

## Contributing

[Run from source](https://github.com/RasmusRynell/ax-devil/blob/main/docs/installation.md#run-from-source), then:

```bash
make check
QT_QPA_PLATFORM=offscreen make test
```

[Architecture](https://github.com/RasmusRynell/ax-devil/blob/main/docs/architecture/overview.md) ·
[Module map](https://github.com/RasmusRynell/ax-devil/blob/main/docs/architecture/module-map.md) ·
[Testing](https://github.com/RasmusRynell/ax-devil/blob/main/docs/runbooks/testing.md) ·
[Issues](https://github.com/RasmusRynell/ax-devil/issues)

Configuration and logs may contain camera credentials.
[Redact them before sharing.](https://github.com/RasmusRynell/ax-devil/blob/main/docs/settings.md#configuration-and-local-data)

## License

[MIT](https://github.com/RasmusRynell/ax-devil/blob/main/LICENSE)

Demo footage and ground truth: HighwayDrive from [UVG-VCM](https://tie-ultravideo.rd.tuni.fi/UVG-VCM/index.html),
Ultra Video Group, Tampere University, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The DET lane is
simulated from that ground truth.
