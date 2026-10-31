# 08 Settings, CLI and Run Modes

Owns: the settings model and scopes, the CLI surface, run modes. Sources: `docs/settings.md`,
`docs/domain/invariants.md#settings-and-shortcuts`, `settings/config_manager.py` (`DEFAULT_CONFIG`), `src/ax_devil/cli.py`,
and `ax-devil <command> --help` run from a worktree at the reference commit.

## Settings model

- **SHOULD** declare every setting once in a table: key, type, default, scope, live or restart, validation, label,
  page. Defaults, config parsing, dialog rows, the restart marker and View-menu toggles are generated from it.
  *Current:* `DEFAULT_CONFIG`, enums with `from_config`, `GlobalSettings` properties and hand-built dialog rows.
- **MUST** keep one immutable settings state replaced whole; consumers subscribe, never poll.
- **MUST** apply on OK: validate every field, write atomically, then notify; a failed save changes nothing.
- **MUST** apply live where the stack allows; settings that cannot carry one shared restart marker.
- **MUST** keep startup storage folders active until restart, except recovery copies and recents, which follow the
  newly saved folder.
- **MUST** let changed defaults prefill only new items; environment preferences (connection timeout) are read when
  used and never stored on items.
- **MUST** accept `$NAME` references in connection, analytics, network-scan and storage fields and keep them as
  written. On save, validate numeric and storage references; leave connection text and credential references
  unresolved until used (`docs/settings.md`). Stronger validation is an owner-approved change. Mask literal passwords,
  show references. CLI options override config and environment for one launch. Credentials and the expansion
  rules: [13](13-security-packaging-ops.md#credentials), [13](13-security-packaging-ops.md#threat-model).

## Scopes

| Scope | Lives in |
|-------|----------|
| App preference (theme, text size, cache memory, scan on startup, overlay preferences) | Config |
| Defaults for new items (device, broker, DataHub, scan range and logins) | Config |
| Saved per workspace (items, bookmarks, reviewed marks) | Workspace file |
| Per view, session only (catalog selection, overlay details, sticky/sync/filter) | Memory; defaults for new views in config (IN-07) |
| Default catalog (the only remembered catalog choice) | Catalog store |
| Shortcut overrides | Config |
| Window size and maximized state | Separate window-state store |

## CLI surface

| Command | Behavior |
|---------|----------|
| `ax-devil` | Launch (start panel or restored recovery copy); global `--log-level`, `--config` |
| `local`, `live`, `playlist <resolver>` | Build one item into a new Untitled workspace |
| `open WORKSPACE_FILE` | Open a saved workspace |
| `list-handlers` | List file overlay handler types |
| `catalog`, `catalog list/check/render/new/use/reference` | Open the app with the catalog window, or headless catalog work |
| `plugins install/update/remove/list` | Manage plugins from the base app |
| `clear-cache` | Remove caches with confirmation |

- **MUST** keep CLI commands thin item builders using the same intake as dialogs; failures surface in the app
  (`docs/architecture/workspace.md#resolution`).
- **MUST** let `--help` and non-rendering commands run without initializing the GUI runtime. Rendering commands
  (`catalog check`, `catalog render`) may initialize the shared renderer on an offscreen or private surface with no
  visible window, and document their display and GPU prerequisites.
- **MUST** keep `--help` the only documentation of flags; top-level help links docs, AGENTS.md and skills.
- **MUST** give every agent-editable surface a headless command that exits non-zero with a message naming the location.
- **SHOULD** add `--json` output to listing commands; not present today.

## Run modes

| Mode | Requirement |
|------|-------------|
| Normal | INFO logging by default |
| Developer | One command runs from source with DEBUG logging |
| Isolated (agents, CI) | [09](09-testing.md#isolation-must) |
| Restart | Relaunch with the same global options |
