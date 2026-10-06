# Changelog

All notable changes to ax-devil are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

First public release, planned as 0.1.0.

### Added

- Linux desktop workspace for inspecting video together with analytics overlays, with drag-to-split viewer layouts.
- Offline recordings with overlays from CVAT, MOT, UVG-VCM, Axis ADF, and ONVIF XML files.
- Live Axis cameras over RTSP, with overlays from embedded RTSP/ONVIF metadata, MQTT, or DataHub WebSocket.
- Object inspector with type filtering, attributes, per-object history, and scene events.
- Playlists that group recordings and comparison lanes into a review sequence.
- Export of video with overlays, preserving source frame timing. Exports contain no audio.
- Render catalogs controlling overlay appearance, with built-in Standard, Minimal, Chunky, Glass, and Tracking
  styles, and a catalog viewer for previewing, copying, and applying them.
- Decoder and playlist resolver plugins, installed into isolated environments with `ax-devil plugins`.

### Known limitations

- DataHub HTTPS/WSS does not verify device certificates: traffic is encrypted, but the device identity is not
  authenticated. Use it only on trusted camera networks.
- High live frame rates or bursts can exhaust the frame buffer and stall live playback. An error is logged when this
  starts; reducing the source frame rate avoids it.
- Seeking in long-GOP 4K video can temporarily use memory beyond the configured decoded-frame cache allowance.
