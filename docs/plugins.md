# Creating and using plugins

This guide covers installing, writing, packaging, and upgrading plugins. For what plugins can do, see the
[README](../README.md#extend-it-with-plugins); for contracts, see the [plugin architecture](architecture/overview.md#plugin-system).

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

## Trust

Install only plugins and dependencies you trust. Installation/build steps and validation
can execute arbitrary Python; loaded plugins run in the application process. The separate
environment isolates dependencies, not permissions, and is not a security sandbox.

## A complete minimal decoder plugin

Create a separate directory:

```text
example-plugin/
├── pyproject.toml
└── example_plugin.py
```

### pyproject.toml

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "example-plugin"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["ax-devil"]

[project.entry-points."ax_devil.decoder_plugins"]
example = "example_plugin:ExamplePlugin"

[tool.setuptools]
py-modules = ["example_plugin"]
```

### example_plugin.py

```python
from typing import Any

from ax_devil.modules.plugin_system import DecoderPlugin, PayloadToSceneDecoderDefinition
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import Scene, TimeSlice


class ExampleDecoder(PayloadToSceneDecoder):
    """Decode a frame number into an empty scene for that frame."""

    def decode(self, payload: Any) -> Scene:
        """Accept a payload such as {\"frame\": 42}."""
        frame = int(payload["frame"])
        return Scene(time_slice=TimeSlice(start=frame, end=frame))


class ExamplePlugin(DecoderPlugin):
    """Expose the example payload decoder to ax-devil."""

    @classmethod
    def required_api_version(cls) -> int:
        """Declare the plugin API this implementation supports."""
        return 1

    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
        """Declare the Scene model version this decoder builds Scenes for."""
        return (1, 0)

    @classmethod
    def plugin_id(cls) -> str:
        """Return a unique identifier within the decoder family."""
        return "example"

    @classmethod
    def display_name(cls) -> str:
        """Return the name shown by ax-devil."""
        return "Example decoder"

    @classmethod
    def payload_to_scene_decoders(cls) -> tuple[PayloadToSceneDecoderDefinition, ...]:
        """Publish the handler and a decoder factory for each session."""
        return (
            PayloadToSceneDecoderDefinition(
                handler_type="EXAMPLE_FRAME",
                decoder_factory=ExampleDecoder,
                display_name="Example frame payload",
            ),
        )
```

Install this directory using `plugins install`, then launch normally. Installation checks that the entry point
imports and registers successfully. This example contributes a **payload** handler, for decoded streaming messages;
it does not contribute a file handler to `list-handlers` or draw any objects yet. Add entities to the returned Scene
to implement your format.

Decoders may put any picklable debug data, in any shape, in `Scene.debug` (frame-level) or `Observation.debug`
(per object). ax-devil only displays it, as a generic tree in the hover card and entity side panel.

## Plugin packaging requirements

- Give each distribution its own import namespace; avoid a shared top-level package named `plugins`.
- Declare every Python runtime requirement in `[project].dependencies`, for example `"protobuf>=5"`.
  Include `"ax-devil"` with any required version constraints. The installer supplies the editable app checkout
  in development, or pins the exact base app release for standalone installations. Plugins cannot upgrade the app.
- Declare supported Python versions honestly. The installation uses the Python version running ax-devil.
- Point an entry point at your plugin class or `PLUGIN_CLASS`. Declare `required_api_version()` explicitly.
- Decoder plugins must declare `scene_model_version()` as `(major, minor)`. ax-devil exposes its own as
  `ax_devil.modules.scene.model.SCENE_MODEL_VERSION`. A plugin loads when the major matches and its minor is not
  newer. ax-devil bumps the major when Scene fields are removed, renamed, or change meaning, and the minor when fields
  are added. Plugins that fail to load are listed in a dialog at startup. Derived caches rebuild automatically when
  the Scene model version changes; bump `artifact_version` only when your own decoded output changes.
- Keep plugin IDs and handler types unique. Duplicate or incompatible registrations fail installation.
- For file decoders, override `file_to_scene_decoders()` and return `FileToSceneDecoderDefinition` objects. Set
  `file_extensions` (lowercase, such as `(".txt",)`) so the app can pick your decoder when it is the only one that reads
  a chosen overlay file; leaving it empty keeps the decoder available for every file.
- For playlist resolvers, use the `ax_devil.playlist_resolver_plugins` entry-point group and subclass
  `PlaylistResolverPlugin`. Settings-widget and optional Click-command contracts also apply.
- External programs such as `protoc` are not Python dependencies; document their installation separately,
  or remove the runtime requirement by generating the needed files when building your package.

Private indexes and credentials can come from uv's user configuration or environment. To scope a named index to
one plugin installation and preserve it for subsequent updates, pass `--index NAME=URL` to `plugins install`.
The URL is stored in the private plugin project; keep credentials in uv's credential store or environment. Do not
put credentials in plugin metadata or persisted `--index` URLs. A dependency package's own `[tool.uv.sources]` is not an installation recipe:
declare real package dependencies and configure required indexes at the installation level.

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

Before selecting a prepared environment, the launcher checks the app version and source, Python identity, the
installed versions of the app's dependencies, and editable app project metadata. If any changed, that launch rebuilds
the same plugin selection for the current app; this is the only time startup runs the installer, and it may need
network access. If the rebuild fails, or the environment/source is missing, it warns and starts the **current base app
without managed external plugins**, showing the appropriate `plugins update` command. A failed rebuild is not retried
automatically; `plugins update` retries it. Simultaneous launches wait for one rebuild. It never runs the old app from
an outdated plugin environment.

An incompatible plugin may prevent rebuilding after an app upgrade. Plugin selections and the previous environment
remain intact; remove the incompatible plugin, choose a compatible release, or retry with `--upgrade`. Keeping the
old environment does not roll back the base app. Management commands always run in the base environment so they
remain available for repair. Removing all plugins returns launches to the base app.

## Storage and supported installations

Each editable checkout or standalone base Python environment owns
`$XDG_DATA_HOME/ax-devil/plugins-<location-hash>/` (normally under `~/.local/share`). Checkouts and standalone installs
have independent plugin selections. The identity is stable across upgrades at the same location; moving a checkout
or installing into a different environment starts a separate selection.

The `current` link selects a standard uv project containing `pyproject.toml`, `uv.lock`, and `.venv`. Its project
metadata records selections and the host fingerprint independently of the disposable `.venv`, so `plugins update`
can rebuild a missing interpreter. Do not delete the project metadata if you want to retain selections.

Changes are activated only after the app and selected plugins pass validation. Failed preparations leave the
working installation intact. The environment a change replaces is kept for already-running processes, including after
ordinary removal; older ones are deleted. Close app instances before `plugins remove --all`, which deletes them all.
Editable source edits are shared across environments; this is not source-code rollback.

Supported app sources are editable checkouts, releases from a package index, and apps installed directly from a wheel
file or Git URL. Plugin environments install the same app from the same place, so that index, wheel file, or
repository commit must stay available. A non-editable install from a local directory is rejected, because the
directory can change while the version stays the same.

Plugin management currently targets Linux and requires Unix file locking and the usual Linux virtual-environment
layout. Other platforms can launch the base app where its dependencies support them, but are not validated plugin
installation targets. The environment isolates dependencies, not permissions. All selected plugins share Python
and must have mutually compatible dependencies.

## Decoder classification filters

`FilterOption` accepts an Entity predicate for Scene filtering. To support whole-file object lists, declare a
classification policy using `make_classification_predicate(...).build_option(id=..., label=...)` or
`make_other_classification_predicate(...).build_option(...)` from `ax_devil.modules.filtering.predicate_utils`.
These policies match both real observations and the classification types recorded in object histories, including
unclassified objects via `include_empty=True`. `ClassFilterSpec.build_option()` declares the same capability.

A config containing an Entity-only predicate supports frame filtering; its whole-file scope is disabled because
history does not contain geometry, confidence or other observation data needed to evaluate an arbitrary predicate.
