# 15 Open Decisions

Settle each before its consumer; recommendations apply unless the owner objects. D5/D16 are withdrawn, D13 is AP-06,
and D20 belongs to M2. Decisions already taken are in [14](14-build-order.md).

## Stack sign-off, by M9

| ID | Question | Recommended answer |
|----|----------|--------------------|
| D0 | Does the owner confirm the stack taken at the start (Rust backend, React frontend, Electron shell)? | Yes, once the owed spikes ([stack choice](14-build-order.md#d0-stack-choice)) show it holds: live capture time (M5), one surface for video and overlays (M2), plugins (M7), platforms (M8) |

## By M2

| ID | Question | Recommended answer |
|----|----------|--------------------|
| D1 | How do plugins (file decoders, message decoders, playlist resolvers) run, and do existing third-party Python plugins keep working (PL-05)? | Settled by D29: the built-in ones are native code and other data enters through plugin processes (one manifest of typed handlers; the plugin owns its environment through `command`); authors port third-party plugins to the new contract and the Python bridge (PL-05) stays not planned ([05](05-formats-and-extensions.md#plugin-runtime)) |
| D15 | Do we write a throwaway script in the old Python app that dumps what it decodes and draws, to compare the new code against? | Yes; commit the outputs with a note on where they came from |

## By M3

| ID | Question | Recommended answer |
|----|----------|--------------------|
| D18 | When may a workspace file use `$VARIABLE` references, which read secrets such as passwords from the environment ([13](13-security-packaging-ops.md#threat-model))? | Decided: expanded silently for the person at this machine, with no prompt and no host binding, as in the old app; text a remote viewer (a holder of the server password) types or sends stays literal and is never expanded. [human] "Silent like Python"; "Expand for local use only" |

## By M4

| ID | Question | Recommended answer |
|----|----------|--------------------|
| D4 | Should a playlist resolver (the plugin that turns a folder or file into a playlist) declare its settings as data, so the app and the command line both build the form from it ([05](05-formats-and-extensions.md#resolver-contract))? | Yes; tested against both built-in resolvers |
| D6 | How is the app packaged, which FFmpeg build does it ship, and may it include a GPL video encoder for export? | A single Linux package (AppImage or Flatpak) plus a source build; FFmpeg as a dynamically linked LGPL build; no GPL encoder; no Python (D1 keeps no bridge) |

## By M5

| ID | Question | Recommended answer |
|----|----------|--------------------|
| D8 | May literal passwords in the config and in workspace files be stored in the operating system's keychain? | Yes, as an option; `$VARIABLE` references stay the default |
| D12 | Should the app check the HTTPS certificate of a DataHub (the camera-side data service), which the old app does not? | No, as before; add certificate pinning as an option later |
| D14 | Which libraries handle the live connections (RTSP video, MQTT, WebSocket) and the camera's device API? | Third-party RTSP and MQTT clients that give each frame its camera capture time, plus a small Axis device-API module that sets up and removes the temporary MQTT publisher (LV-08) |

## By M8

| ID | Question | Recommended answer |
|----|----------|--------------------|
| D2 | Which platforms are supported? | Linux fully, including plugin management (Linux-only today); Windows and macOS build and pass the test suite |
| D11 | Should the app be translatable? | No, English only; keep the strings grouped per module so it stays possible |

## By M9

| ID | Question | Recommended answer |
|----|----------|--------------------|
| D17 | Delete `docs/rebuild/` once the rewrite reaches parity, or keep it as history? | Delete it once every rule has its new home ([11](11-docs-structure.md#retiring-docsrebuild)) |
