# 13 Security, Packaging and Operations

## Credentials

Owns credential handling; [08](08-settings-cli.md) links here.

| Rule | Level | Source |
|------|-------|--------|
| No credentials in the repo; tests and agents use `AX_DEVIL_TARGET_*` and `AX_DEVIL_MQTT_BROKER_*` or config overrides | MUST | `AGENTS.md` Project Rules |
| `$VARIABLE` references are the default for device and broker credentials | MUST | `DEFAULT_CONFIG` in `settings/config_manager.py` |
| Workspace files and recovery copies store credentials as entered; new files private to the user | MUST | `docs/architecture/workspace.md#file-format` |
| Warn in the item dialog when a literal password will be saved into a workspace file | MUST (ST-06) | new |
| Literal secrets in config and workspace items may live in the OS keychain, referenced from the file | SHOULD (D8) | new |
| Logs redact credentials | MUST, owned by [03](03-runtime.md#logging-must) | — |
| Config, cache and log folders user-owned; never load others' caches | MUST | `docs/settings.md#configuration-and-local-data` |

## Threat model

A workspace file from someone else is untrusted input. *Current:* a Live Stream Item's `$VARIABLE` references are
expanded while resolving (`docs/architecture/workspace.md#workspace-items`), and camera snapshots on hover and the
saved-login check contact the item's host without Start (`docs/usage.md#sidebar-views`, `docs/settings.md`). A shared
workspace can therefore name an attacker host and `$SOME_SECRET` and receive it.

**MUST** (ST-06, D18; a behavior change from the reference). An allowlist of names alone fails: a hostile workspace
can pair its own host with `$AX_DEVIL_TARGET_PASS`. Recommended default:

- A credential reference expands only together with its matching host reference (`$AX_DEVIL_TARGET_USER/PASS` with
  `$AX_DEVIL_TARGET_ADDR`; the `$AX_DEVIL_MQTT_BROKER_*` set likewise, but the camera relays the broker login, so
  the camera host needs the user's confirmation too), or for a resolved host the user confirmed for that
  credential. Any other reference needs the same confirmation.
- The confirmation is asked once, on Start, showing the resolved host. It is stored in the app's user state (config),
  keyed by reference name and resolved host, never in the workspace file.
- Opening a workspace sends no credentials (snapshots, login checks) to a host the user has not started or confirmed.
  Started: a viewer was opened for the host in this run. Confirmed: the host is the reference bound to the camera host,
  or the user confirmed a reference for it. A hover or a poll asks a camera only once per cooldown.

## Network (MUST)

- Network scan sends no credentials until a device answered as Axis; then only over the identifying protocol, as
  HTTP Digest (`device_discovery/network_scan.py`). The startup scan runs at reduced load, can be turned off and
  signs in to nothing: only a scan the user starts does.
- DataHub: Digest before Basic; a rejected Digest never falls back to Basic; redirects rejected; no automatic
  HTTPS-to-HTTP fallback (`data_sources/README.md#live-transports`). Certificate verification: D12.
- Device API defaults to HTTPS. No telemetry. Regular tests never use the network.

## Sharing (MUST)

Owns who may reach a backend and the limits that keep one peer or crowd from using it up. Listeners and principals:
[overview](../architecture/overview.md#listeners-and-principals); what each role may do in a Session:
[sessions README](../../crates/sessions/README.md).

- Shared listeners grant nothing by cookie or IP address: every credential is a bearer secret (128 random bits,
  compared in constant time), so a rebound DNS name or a cross-site page gains nothing. Only the Local listener checks
  Host (a loopback name) and Origin (absent, or `http://` and the Host in full).
- A secret travels in the URL fragment (`#join=`, `#password=`), which the page moves to `sessionStorage`, then in
  `Authorization: Bearer` on HTTP and in `Hello` on the socket. Never in a query string or a log.
- A chosen server password (`AX_DEVIL_SERVER_PASSWORD`) is printable ASCII without spaces, since a browser cannot send
  anything else in a header; `serve` refuses to start otherwise.
- Every response on every listener carries `frame-ancestors 'none'`, `default-src 'self'` and `img-src 'self' data:`
  (small icons are inlined as `data:` images), and `X-Frame-Options: DENY`. The smoke test asserts the release app
  breaks none of it.
- Plain HTTP on a non-loopback address is allowed and logs a warning at startup. TLS is a reverse proxy's job:
  it targets the shared port, never the Local port, and the server is told its public address (`--public-url`).
- Join links need the release build: a development build serves no frontend, so `serve` refuses to start and the
  desktop opens no listener.

| Limit | Value |
|-------|-------|
| Open sockets, all / per IP | 1024 / 16; every client of a proxy has the proxy's IP, so only the first applies when `serve` is started with the behind-proxy setting |
| Sockets still waiting for `Hello`, all / per IP | 64 / 4, and `Hello` within 5 s |
| Frame, message | 16 KiB, 64 KiB |
| Sessions per server, seats per Session | 64, 32 |
| Streams per seat, per Session | 8, 64 |
| Liveness | The server pings every 10 s; 25 s of silence is a disconnect and starts the 30 s grace |
| Sending | Each send has a 10 s deadline; a timeout is a disconnect |
| Per seat | Cursors 60/s, the excess dropped; views 60/s, only the latest kept; commands 10/s, the excess closes the socket |

A close the server chose is final and the client stops: 1007 invalid message, 1008 not allowed, 1009 too big, 1013
full, 4401 invalid password or link, 4404 session ended or unknown, 4409 seat replaced (the Session is open in another
tab), 4429 too many commands. A lost connection reconnects. The playback socket closes with 4401 for a ticket that is
not valid and 4403 when its authority is revoked.

### Known limits

Documented, not fixed.

- **The machine is the trust boundary.** Any process that can reach the Local listener, other OS users included, is
  the local owner: it can drive the owner's Sessions and read the files the app can. The Host and Origin checks defend
  against browsers and DNS rebinding; they do not authenticate a person. A per-launch credential is the known fix and
  is not implemented. Startup content is consumed by the first new Local owner Session, not bound to a
  requesting window. Video/playlist arguments are not forwarded when reusing an existing shell or backend.
- **Remote video is one JPEG per frame.** About 40 to 65 Mbit/s per 1080p30 watcher, depending on the scene, and
  compressed once whatever the number of watchers, so 1 Gbit/s carries 15 to 25 at full rate and more see fewer
  frames. Inter-frame (H.264) delivery is future work.
- **Edit access has no UI.** The backend supports an `Edit` access row for editing together, but nothing exposes it,
  and its projection lists only the Owner's open items. This is not a command-authorization guarantee that a
  retained item id cannot be reopened; settle that policy before exposing editing.
- **Snapshot cost is unmeasured** at 32 seats under 30 Hz scrubbing.

## Plugin trust (MUST)

State that plugins run with the user's permissions and are not sandboxed (`docs/plugins.md#trust`). Keep credentials
out of plugin manifests and the registry.

## File safety (MUST)

Atomic writes and the rule about built-in resources: [05](05-formats-and-extensions.md#format-policy). Workspace paths:
`docs/architecture/workspace.md#file-format`.

## Packaging

| Topic | Current | Rebuild guidance |
|-------|---------|------------------|
| Channel | PyPI wheel; `uv tool install`, pip, source with `uv sync --locked` | D6; default: one self-contained Linux package (AppImage or Flatpak) plus source build; CLI named `ax-devil` |
| Native dependencies | Qt and FFmpeg system libraries per distro | Bundle where licenses allow; one installation page for the rest |
| Versioning | SemVer, Keep a Changelog, tag matches package version | Keep |
| Release | Tag, CI, clean-container install check, publish (`docs/runbooks/testing.md#package-artifacts`) | Keep the clean-environment check: install as documented, render catalogs, install the example plugin, play a generated video in the isolated environment, upgrade, check plugins still load |
| Upgrades | Plugins do not depend on the app | An upgrade changes no plugin; one that no longer loads is listed |

### Desktop package

The backend ships beside the app's files, not inside an archive, so it can be started as a process; the app's own
Electron also runs the hidden renderer that export draws with, from the same folder. FFmpeg is a pinned LGPL shared
build, linked dynamically so users can replace it; each release ships the matching FFmpeg source. It has no x264 (GPL):
the H.264 encoder is OpenH264 on Linux and Windows and VideoToolbox on macOS. Pushing a `v*` tag drafts a GitHub
release.

Builds are unsigned. Linux: `chmod +x` the AppImage; where FUSE is missing run it with `--appimage-extract-and-run`;
on Ubuntu 24.04+ the Chromium sandbox may need `--no-sandbox`. Windows: SmartScreen "More info", then "Run anyway".
macOS: System Settings, Privacy & Security, "Open Anyway", or `xattr -dr com.apple.quarantine ax-devil.app`.

D6 and D29: plugins are the user's own programs and bring their own environment (`command`), so a bundle ships no interpreter for them.

## Observability

- Logs ([03](03-runtime.md#logging-must)); user-relevant failures in the UI ([03](03-runtime.md#error-model-must)).
- Diagnostic tools stay separate from the app (`tools/README.md`).
- "Open logs folder" / "copy diagnostics": AP-06.

## Licensing

- App license MIT (`LICENSE`). Bundled icons are Lucide (ISC); ship the license file
  (`src/ax_devil/resources/icons/`). Demo footage and dataset attributions: `README.md`, `docs/datasets/`.
- **MUST** check every new dependency's license and the distribution terms of bundled binaries. The encoder is whichever H.264
  encoder the FFmpeg build has (libx264, libopenh264, VideoToolbox); libx264 is GPL, so a bundle ships none (D6).
