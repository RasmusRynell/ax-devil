# Parity: plugins, settings, application, sharing and developer tooling

Row format: [parity](../parity.md). Deviations: [app deviations](app-deviations.md).

## Plugins

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| PL-01 | Decoder plugins (file, payload) and playlist resolver plugins | M | done | `crates/plugins/tests/`, `tests/it/plugins.rs` | Decoder handlers read a file or, with `input: records`, one record at a time (live sources, lines of a file); resolver handlers ([plugins](../plugins.md), D29) |
| PL-02 | Built-in decoders and resolvers listed by `list-handlers` and `playlist --help` | M | done | `src/cli/tests.rs`, `src/routes/handlers.rs`, `crates/handlers` tests, `crates/handlers/tests/decoders/adf_*.rs`, `crates/handlers/src/decoders/iso8601.rs`, `resolvers/ResolverForm.test.tsx` | MOT, UVG-VCM, ONVIF XML, CVAT, ADF beta/v1 (frame and consolidated), plugin decoders; both resolvers. ISO timestamp reference: the Python plugin call chain on CPython 3.12.15; syntax exceptions are recorded in [app deviations](app-deviations.md) |
| PL-03 | Install, update, remove, list from CLI and Plugins view | M | done | `tests/it/plugins.rs`, `src/cli/tests.rs`, `src/routes/plugins.rs`, `plugins/PluginsView.test.tsx` | `plugins install/update/remove/list/check` from the CLI and the Plugins view; applied to the running app |
| PL-04 | Load failures never stop the app; listed | M | done | `tests/it/plugins.rs`, `plugins/PluginsView.test.tsx` | `plugins list` and Plugins view show failures; a failed run marks its lane Incomplete |
| PL-05 | Unchanged third-party Python plugins run (bridge); not planned by default | S | todo |  |  |

## Settings and application

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| ST-01 | Settings pages; restart marker | M | done | `shell/SettingsDialog{,.table}.test.tsx`, `desktop/smoke-appearance.cjs`, `shell/numericInput.test.ts`, `shell/recoveredPreferences.test.tsx`, `client/devicePersistence.test.tsx`, `desktop/smoke-preferences.cjs`, `settings/src/tests{,/file}.rs`, `src/tests.rs`, `tests/it/catalog_cli.rs`, `desktop/smoke-start-error.cjs` | Raw fallback survives unrelated saves; a different submitted value replaces it; explicit displayed-fallback repair; All pages; edits staged until OK; Cancel keeps runtime settings unchanged; storage validated at service startup; cache clearing validates only its selected cache; other utilities can load without it |
| ST-02 | Quick Setup on first start | M | done | `shell/QuickSetupDialog{,.persistence}.test.tsx`, `client/settings.test.tsx`, `desktop/smoke-appearance.cjs` | Opens on first run and from Help; Server refusals retain unconfirmed previews; Device failure reports accepted completion state |
| ST-03 | Theme (light, dark, auto) and text size (system, small to larger) | M | done | `crates/settings` tests, `App.test.tsx`, `client/settings.test.tsx` | Stored on the device, not through `/api/settings`; Quick Setup previews live, Settings applies on OK; zoom stays the browser's |
| ST-04 | `$VARIABLE` references | M | done | `settings/src/reference_tests.rs`, `shell/SettingsDialog.test.tsx`, `shell/AddLiveStreamDialog.test.tsx` | Every field takes one, kept raw; unset gives empty or the default, with a warning; a number or folder that does not resolve is refused on save |
| ST-05 | Keyboard shortcuts and overrides | M | done | `crates/settings` tests, `kit/shortcut.test.ts`, `shell/ShortcutsDialog.test.tsx`, `desktop/smoke-native-input.cjs`, `shell/SettingsDialog.test.tsx`, `shell/Shell.test.tsx` | Command table; unavailable viewer actions stay disabled; malformed shortcut fallback retains raw overrides until explicit repair; conflict checks, live keys; Meta portable grammar/save/reload (`shortcuts::tests::meta_binding_survives_save_reload_and_conflicts_are_refused`, shared key vectors), F2 capture, native shortcut labels and Meta dispatch (`ShortcutsDialog.test.tsx`, `smoke-native-input.cjs`); editor from General, OK saves, Cancel ignored while saving |
| ST-06 | Warn before saving a literal password (new); workspace references expand silently (D18) | M | done | `shell/{SettingsDialog{,.table},AddLiveStreamDialog}.test.tsx`, `crates/workspace` live tests | Camera and broker passwords mask and warn on a literal, not a `$NAME` |
| AP-01 | Uncaught errors logged; continue or close | M | done | `shell/UncaughtErrors.test.tsx`, `desktop/uncaught.test.cjs` | The page reports its errors to the backend's log |
| AP-02 | Restart from inside the app | M | done | `shell/Shell.test.tsx`, `desktop/args.test.cjs` | A setting that needs a restart offers to relaunch the desktop app on its own backend; other windows are told |
| AP-03 | `clear-cache` | S | done | `src/disk_cache.rs`, `src/cli/clear_cache.rs` | `--type main/all`; lists, asks, removes; only ax-devil's own cache files |
| AP-04 | About dialog | S | done | `shell/Shell.test.tsx` |  |
| AP-05 | Custom window frame: in-page title bar with window controls | S | done | `shell/WindowControls.test.tsx`, `desktop/smoke-{native-window,restored-bounds}.cjs` | CHR-007: native drag and all eight resizes on private Openbox; X11 restore-drag follows the reference's threshold, placement and manual fallback; CHR-008 keeps restored controls on screen |
| AP-06 | Open logs folder / copy diagnostics (new) | S | done | `shell/Shell.test.tsx` | In About |

## Sharing and remote use (new)

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| SH-01 | Join, reconnect and revoke access | M | done | `crates/sessions/tests/session/`, `tests/it/{session_socket,playback_ws}.rs` | A cut connection (TCP proxy) resumes its seat; a stopped sharing refuses it; silent loss takes 25 s |
| SH-02 | Follow or explore independently | M | done | `crates/sessions/tests/session/{revocation,matrix}.rs`, `viewer/PlaylistFollower.test.tsx`, `desktop/smoke-sharing.cjs`, `src/routes/playback/inspect/tests.rs` | Video and playlist. Inspection is gated like data: a follower outside its window gets none. Live policies follow live |
| SH-03 | Audience privacy and settings scope | M | done | `tests/it/{access,playback_catalogs}.rs`, `src/session/project.rs`, `crates/settings/src/scope_tests.rs` | Host paths and settings scoped; a watcher sees a user catalog once its Session's host viewer asks for it, until it leaves |
| SH-04 | Browser and remote Electron connection | M | done | `desktop/{smoke-remote,smoke-handoff,links.test,remote.test}.cjs`, `tests/it/workspace_files.rs`, `session/Notice.test.tsx` | Server frontend, password recovery, join and hosting failure reasons (`src/share.rs`); a second launch's link, server or command reaches the running app. Two-machine outages and OS `ax-devil://` registration on Windows and macOS were tested by hand by the owner |

## Developer tooling

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| DT-01 | Isolated GUI test environment for agents and CI | M | done | `make test-desktop` | `scripts/isolated`: private home and display, user-file guard; Linux |
| DT-02 | UI screenshot harness | M | done | `make test-desktop`, `desktop/fit-coverage.test.cjs` | `desktop/smoke*.cjs`: windows, menus, dialogs, lanes and Objects in both themes at the window minimum and 200% zoom, the video and sharing scenarios |
| DT-03 | Rendering benchmarks with A/B against a ref | S | in progress | `make bench` | `overlays` bench of the Sample Item encoder; numbers on stdout, nothing asserted |
| DT-04 | Timestamp alignment report | S | todo |  |  |
| DT-05 | Release check in clean environments | S | todo |  |  |
| DT-06 | Demo recorder | S | todo |  |  |
