# Usage reference

Details behind the [README](../README.md) walkthrough. Commands are shown as `ax-devil`; from a source checkout,
prefix them with `uv run`.

## Video and overlays

`ax-devil local --help` lists file-opening options. `--overlay` requires `--handler-type`;
`ax-devil list-handlers` lists the available values.

The **media tools panel** holds filtering, object inspection, the event log, the render catalog choice and export.
Open it with **View → Media Tools Panel** (`Ctrl+B`) or by double-clicking or dragging the handle on the right edge of the video.

Press **F** on a lane to show it fullscreen; **F** or **Esc** restores it, and **F11** toggles the application
window. Playback shortcuts can be changed in **Settings → General**.

## Live cameras

New connections use the credentials, overlay mode, and decoder from **Settings → Stream defaults**;
`ax-devil live --help` lists explicit overrides.

By default, credentials come from environment variables, which keeps them out of the configuration file:

| Variable | Used for |
|---|---|
| `AX_DEVIL_TARGET_ADDR`, `AX_DEVIL_TARGET_USER`, `AX_DEVIL_TARGET_PASS` | Camera host, username, and password |
| `AX_DEVIL_MQTT_BROKER_ADDR`, `AX_DEVIL_MQTT_BROKER_USER`, `AX_DEVIL_MQTT_BROKER_PASS` | MQTT broker host, username, and password |

```bash
export AX_DEVIL_TARGET_ADDR=192.168.1.100 AX_DEVIL_TARGET_USER=root AX_DEVIL_TARGET_PASS=secret
ax-devil live
```

Any **Stream defaults** text field accepts a `$VARIABLE` reference in place of a value. Command-line options override
both.

DataHub defaults to HTTPS/WSS with certificate verification disabled: traffic is encrypted, but the device
identity is not authenticated. Use it only on trusted camera networks. HTTP/WS is selectable with no automatic
fallback, and redirects are rejected, so connect directly to the device host. This applies to DataHub only,
not RTSP or MQTT.

## Playlists

`ax-devil playlist --help` lists the available resolvers; each resolver has its own `--help`, for example
`ax-devil playlist folder_pair --help`. To write a resolver, see the
[write-plugin skill](../.agents/skills/write-plugin/SKILL.md).

The [UVG-VCM walkthrough](datasets/uvg-vcm.md) pairs the dataset's official MP4s with native JSON annotations,
including tracking IDs and available segmentation polygons.

## Overlay appearance

Built-in catalogs are read-only; make a copy before editing. A catalog choice applies to the current view; the
**⋯** menu next to it applies the choice to all open views or makes it the default for new ones. **Reload**
rereads the catalog if a file change was not detected.

`ax-devil catalog --help` lists validation, rendering, and catalog-management commands. For authoring,
see the [render-catalog skill](../.agents/skills/render-catalog/SKILL.md#how-a-catalog-is-built);
`ax-devil catalog reference` prints the full language.

## Overlay details

In the media tools panel, open **Details** beside **Catalog** to choose which information this view draws: object
outlines, IDs, class names, confidence, speed arrows, movement badges, attributes, or relations. Changes also update
paused video. For example, hide Confidence in a lane showing human annotations and keep it enabled for model
predictions in another lane. Hidden data remains available in inspection and hover cards.
An overlay whose every score is 1.0, as ground-truth annotations write, opens with **Confidence** off; turn it on
in **Details** to show the scores anyway.

Each view or comparison lane keeps its own choices when changing catalogs; unsupported details are disabled.
**Reset** enables everything. Offline playlists carry the details you change by lane position across entries, on top
of each entry's own defaults. Choices last until the viewer closes and are not saved across app restarts. Export uses the choices selected when export starts.

## Export

The media tools panel's **Export video with overlays** button opens the export dialog. Exports preserve source frame
timing and contain no audio. A failed export reports the error in the dialog and logs; an existing destination
file is replaced only after success.
