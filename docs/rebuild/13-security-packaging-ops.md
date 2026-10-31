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
  `$AX_DEVIL_TARGET_ADDR`; the `$AX_DEVIL_MQTT_BROKER_*` set likewise), or for a resolved host the user confirmed for
  that credential. Any other reference needs the same confirmation.
- The confirmation is asked once, on Start, showing the resolved host. It is stored in the app's user state (config),
  keyed by reference name and resolved host, never in the workspace file.
- Opening a workspace sends no credentials (snapshots, login checks) to a host the user has not started or confirmed.

## Network (MUST)

- Network scan sends no credentials until a device answered as Axis; then only over the identifying protocol, as
  HTTP Digest (`device_discovery/network_scan.py`). Startup scan runs at reduced load and can be turned off.
- DataHub: Digest before Basic; a rejected Digest never falls back to Basic; redirects rejected; no automatic
  HTTPS-to-HTTP fallback (`data_sources/README.md#live-transports`). Certificate verification: D12.
- Device API defaults to HTTPS. No telemetry. Regular tests never use the network.

## Plugin trust (MUST)

State that plugins run with user permissions and installation may execute code (`docs/plugins.md#trust`). Keep index
credentials out of plugin metadata and persisted index URLs.

## File safety (MUST)

Atomic writes and the rule about built-in resources: [05](05-formats-and-extensions.md#format-policy). Workspace paths:
`docs/architecture/workspace.md#file-format`.

## Packaging

| Topic | Current | Rebuild guidance |
|-------|---------|------------------|
| Channel | PyPI wheel; `uv tool install`, pip, source with `uv sync --locked` | D6; default: one self-contained Linux package (AppImage or Flatpak) plus source build; CLI named `ax-devil` |
| Native dependencies | Qt and FFmpeg system libraries per distro | Bundle where licenses allow; one installation page for the rest |
| Versioning | SemVer, Keep a Changelog, tag matches package version | Keep |
| Release | Tag, CI, clean-container install check, publish (`docs/runbooks/testing.md#package-artifacts`) | Keep the clean-environment check: install as documented, render catalogs, install the example plugin, play a generated video in the isolated environment, upgrade, check plugins follow |
| Upgrades | Plugins rebuild after app changes | Plugins follow app upgrades; failure starts the base app with a retry command |

D6 must be compatible with D1: a self-contained bundle that runs Python plugins needs a bundled or discovered Python.

## Observability

- Logs ([03](03-runtime.md#logging-must)); user-relevant failures in the UI ([03](03-runtime.md#error-model-must)).
- Diagnostic tools stay separate from the app (`tools/README.md`).
- "Open logs folder" / "copy diagnostics": AP-06.

## Licensing

- App license MIT (`LICENSE`). Bundled icons are Lucide (ISC); ship the license file
  (`src/ax_devil/resources/icons/`). Demo footage and dataset attributions: `README.md`, `docs/datasets/`.
- **MUST** check every new dependency's license and the distribution terms of bundled binaries. The encoder today is
  libx264 through FFmpeg, GPL when bundled (D6).
