# Installing and managing plugins

This guide covers installing, upgrading, and removing plugins. For what plugins can do, see the
[README](../README.md#plugins). To write one, see the [write-plugin skill](../.agents/skills/write-plugin/SKILL.md); packaging mechanics of the current
plugin API are in the [toolchain](toolchain.md#plugin-packaging-current-plugin-api).

## Install and run

Use the same launch command before and after installing plugins. For a standalone installation:

```bash
ax-devil plugins install 'example-plugin>=0.1'   # Replace with a real plugin package
ax-devil plugins install /absolute/path/to/plugin.whl
ax-devil plugins install /absolute/path/to/example-plugin
ax-devil plugins list
ax-devil
```

From a source checkout, use the checkout's launch command ([toolchain](toolchain.md#install-and-launch)) for
both commands.

The **Plugins** view in the app does the same from the running app: **Add** is `plugins install`, **Update** is
`plugins install --upgrade-package <package>`, which moves only that package and what it needs, and **Remove** is
`plugins remove`. Each runs through the launcher, so the base app manages the plugins; installs, updates and removals apply after a restart.

`plugins install` accepts one or more package requirements, local wheels, or editable source directories; `--upgrade`
also upgrades every dependency beyond the existing lockfile, and `--upgrade-package NAME` only that package.
Quote requirements containing shell operators such as `>=` or `<`. All selected plugins and the app are resolved
together in one private environment. Packages the app already uses stay at the versions installed with the app, so
plugins run on exactly the dependencies the app was installed and tested with. Conflicting requirements, including a
plugin that needs other versions of those packages, fail installation without changing the active selection.
Built-in plugins are included with ax-devil and need no installation.

Plugin installation never changes the base app environment or a development checkout's dependency manifest or lockfile.
The app carries its own installer, so a separate uv executable on PATH is not required for plugin management,
however ax-devil was installed.

Private indexes and credentials can come from uv's user configuration or environment. To scope a named index to
one plugin installation and preserve it for subsequent updates, pass `--index NAME=URL` to `plugins install`.
The URL is stored in the private plugin project; keep credentials in uv's credential store or environment. Do not
put credentials in plugin metadata or persisted `--index` URLs. A dependency package's own `[tool.uv.sources]` is not an installation recipe:
declare real package dependencies and configure required indexes at the installation level.

## Trust

Install only plugins and dependencies you trust. Installation/build steps and validation
can execute arbitrary code; loaded plugins run in the application process. The separate
environment isolates dependencies, not permissions, and is not a security sandbox.

## Upgrades and recovery

| Task | Standalone command | Behavior |
|------|--------------------|----------|
| Upgrade a uv-installed app | `uv tool upgrade ax-devil` | Updates the base app; plugins follow on the next launch. |
| Upgrade an app installed another way | That package manager's upgrade command | Run in the original app environment; plugins follow on the next launch. |
| Rebuild plugins for the current app | `ax-devil plugins update` | Runs automatically after app changes; run it to retry. Preserves locked versions where compatible. |
| Upgrade plugin packages and dependencies | `ax-devil plugins update --upgrade` | Allows newer versions within selected requirements; keeps the app fixed. |
| Replace a pinned requirement or wheel | `ax-devil plugins install 'example-plugin>=0.2'` | Replaces that distribution's previous selection. |
| Remove a plugin | `ax-devil plugins remove example-plugin` | Rebuilds with the remaining selection. |
| Start over: remove all plugins | `ax-devil plugins remove --all` | Removes every plugin and retained environment of this base app. |

For development, the next launch rebuilds plugins after you pull app dependency changes or change the language runtime. Run
`plugins update` with the checkout's launch command after editing a plugin's dependencies or registration. Editable source changes
appear on restart without a reinstall. `--upgrade` does not pull Git repositories; update their source yourself.
A local wheel or exact version pin stays fixed even with `--upgrade`; install a new wheel or change the requirement
to move it.

When the app, its dependencies or the language runtime change, the next launch rebuilds the same plugin selection, which may need
network access. If that fails, the app starts **without external plugins** and shows the `plugins update` command
to retry. An incompatible plugin can block the rebuild: remove it, choose a compatible release, or retry with
`--upgrade`. Plugin management commands run in the base app, so they stay available for repair.

## Storage and supported installations

Plugins live in `$XDG_DATA_HOME/ax-devil/plugins-<location-hash>/` (normally under `~/.local/share`). Each checkout or
standalone installation has its own plugin selection, kept across upgrades at the same location; moving a checkout or
installing elsewhere starts a new one. Keep the project metadata there if you want to keep your selections.

Close other running ax-devil instances before `plugins remove --all`, which deletes every plugin environment.

Plugin environments reinstall the app from where it came from: an editable checkout, a package index, a wheel file or a
Git URL, which must stay available. A non-editable install from a local directory is rejected, because the directory can
change while the version stays the same. Plugin management targets Linux; other platforms can run the base app but are
not validated for plugins. How the runtime is built and validated is in the [Plugin System
invariants](domain/invariants.md#plugin-system).
