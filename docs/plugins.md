# Installing and managing plugins

This guide covers installing, upgrading, and removing plugins. For what plugins can do, see the
[README](../README.md#plugins). To write one, see the [write-plugin skill](../.agents/skills/write-plugin/SKILL.md).

## Install and run

Use the same launch command before and after installing plugins. For a standalone installation:

```bash
ax-devil plugins install 'example-plugin>=0.1'   # Replace with a real plugin package
ax-devil plugins install /absolute/path/to/plugin.whl
ax-devil plugins install /absolute/path/to/example-plugin
ax-devil plugins list
ax-devil
```

During development, prefix these commands with `uv run` from the ax-devil checkout:

```bash
uv run ax-devil plugins install /absolute/path/to/example-plugin
uv run ax-devil
```

`plugins install` accepts one or more package requirements, local wheels, or editable source directories.
Quote requirements containing shell operators such as `>=` or `<`. All selected plugins and the app are resolved
together in one private environment. Packages the app already uses stay at the versions installed with the app, so
plugins run on exactly the dependencies the app was installed and tested with. Conflicting requirements, including a
plugin that needs other versions of those packages, fail installation without changing the active selection.
Restart the app after plugin changes. Built-in plugins are included with ax-devil and need no installation.

Plugin installation never changes the base app environment or a development checkout's `pyproject.toml` or `uv.lock`.
The app depends on uv and invokes its installed Python module, so a separate uv executable on PATH is not required
for plugin management, including when ax-devil was installed with pip.

Private indexes and credentials can come from uv's user configuration or environment. To scope a named index to
one plugin installation and preserve it for subsequent updates, pass `--index NAME=URL` to `plugins install`.
The URL is stored in the private plugin project; keep credentials in uv's credential store or environment. Do not
put credentials in plugin metadata or persisted `--index` URLs. A dependency package's own `[tool.uv.sources]` is not an installation recipe:
declare real package dependencies and configure required indexes at the installation level.

## Trust

Install only plugins and dependencies you trust. Installation/build steps and validation
can execute arbitrary Python; loaded plugins run in the application process. The separate
environment isolates dependencies, not permissions, and is not a security sandbox.

## Upgrades and recovery

| Task | Standalone command | Behavior |
|------|--------------------|----------|
| Upgrade a uv-installed app | `uv tool upgrade ax-devil` | Updates the base app; plugins follow on the next launch. |
| Upgrade a pip-installed app | `python -m pip install --upgrade ax-devil` | Run in the original app environment; plugins follow on the next launch. |
| Rebuild plugins for the current app | `ax-devil plugins update` | Runs automatically after app changes; run it to retry. Preserves locked versions where compatible. |
| Upgrade plugin packages and dependencies | `ax-devil plugins update --upgrade` | Allows newer versions within selected requirements; keeps the app fixed. |
| Replace a pinned requirement or wheel | `ax-devil plugins install 'example-plugin>=0.2'` | Replaces that distribution's previous selection. |
| Remove a plugin | `ax-devil plugins remove example-plugin` | Rebuilds with the remaining selection. |
| Start over: remove all plugins | `ax-devil plugins remove --all` | Removes every plugin and retained environment of this base app. |

For development, the next launch rebuilds plugins after you pull app dependency changes or change Python. Run
`uv run ax-devil plugins update` after editing a plugin's dependencies or entry points. Editable Python source changes
appear on restart without a reinstall. `--upgrade` does not pull Git repositories; update their source yourself.
A local wheel or exact version pin stays fixed even with `--upgrade`; install a new wheel or change the requirement
to move it.

When the app, its dependencies or Python change, the next launch rebuilds the same plugin selection, which may need
network access. If that fails, the app starts **without external plugins** and shows the `plugins update` command
to retry. An incompatible plugin can block the rebuild: remove it, choose a compatible release, or retry with
`--upgrade`. Plugin management commands run in the base app, so they stay available for repair.

## Storage and supported installations

Plugins live in `$XDG_DATA_HOME/ax-devil/plugins-<location-hash>/` (normally under `~/.local/share`). Each checkout or
standalone installation has its own plugin selection, kept across upgrades at the same location; moving a checkout or
installing elsewhere starts a new one. Keep the project metadata there if you want to keep your selections.

A change keeps the environment it replaces for app instances still running, so close them before
`plugins remove --all`, which deletes every environment. Simultaneous launches wait for one rebuild. Keeping an old
environment never rolls back the base app, and editable plugin sources are shared across environments, so neither
is a source-code rollback.

Plugin environments reinstall the app from where it came from: an editable checkout, a package index, a wheel file or a
Git URL, which must stay available. A non-editable install from a local directory is rejected, because the directory can
change while the version stays the same. Plugin management needs Unix file locking and targets Linux; other platforms
can run the base app but are not validated for plugins. How the runtime is built and validated is in the [Plugin System
invariants](domain/invariants.md#plugin-system).
