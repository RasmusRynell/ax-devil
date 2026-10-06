# Usage reference

Details behind the [README](../README.md) walkthrough. Commands use `uv run ax-devil` from a source checkout;
for a standalone installation, use `ax-devil`.

## Video and overlays

`uv run ax-devil local --help` lists file-opening options. `--overlay` requires `--handler-type`;
`uv run ax-devil list-handlers` lists the available values.

Playback shortcuts can be changed in **Settings → General**.

## Live cameras

New connections use the credentials, overlay mode, and decoder from **Settings → Stream defaults**;
`uv run ax-devil live --help` lists explicit overrides.

DataHub defaults to HTTPS/WSS with certificate verification disabled: traffic is encrypted, but the device
identity is not authenticated. Use it only on trusted camera networks. HTTP/WS is selectable with no automatic
fallback, and redirects are rejected, so connect directly to the device host. This applies to DataHub only,
not RTSP or MQTT.

## Playlists

`uv run ax-devil playlist --help` lists the available resolvers; each resolver has its own `--help`, for example
`uv run ax-devil playlist folder_pair --help`. To write a resolver, see the
[plugin architecture](architecture/overview.md#plugin-system).

The [UVG-VCM walkthrough](datasets/uvg-vcm.md) pairs the dataset's official MP4s with native JSON annotations,
including tracking IDs and available segmentation polygons.

## Overlay appearance

Built-in catalogs are read-only; make a copy before editing. A catalog choice applies to the current view; the
**⋯** menu next to it applies the choice to all open views or makes it the default for new ones. **Reload**
rereads the catalog if a file change was not detected.

`uv run ax-devil catalog --help` lists validation, rendering, and catalog-management commands. For authoring,
see the [catalog language reference](architecture/draw-system.md#catalog-language).

## Overlay details

Open **Media tools**, then **Details** beside **Catalog** to choose which information this view draws: object
outlines, IDs, class names, confidence, speed arrows, movement badges, attributes, or relations. Changes also update
paused video. For example, hide Confidence in a lane showing human annotations and keep it enabled for model
predictions in another lane. Hidden data remains available in inspection and hover cards.

Each view or comparison lane keeps its own choices when changing catalogs; unsupported details are disabled.
**Reset** enables everything. Offline playlists retain choices by lane position across entries. Choices last until
the viewer closes and are not saved across app restarts. Export uses the choices selected when export starts.

## Export

The inspector's **Export video with overlays** button opens the export dialog. Exports preserve source frame
timing and contain no audio. A failed export reports the error in the dialog and logs; an existing destination
file is replaced only after success.
