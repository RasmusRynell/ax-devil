# Plugin contract

Reference for the [write-plugin skill](SKILL.md). Plugin types and registration are described in the
[plugin architecture](../../../docs/architecture/overview.md#plugin-system).

## Minimal package

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
        return 3

    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
        """Declare the Scene model version this decoder builds Scenes for."""
        return (3, 1)

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

Installation checks that the entry point imports and registers successfully. This example contributes a **payload**
handler, for decoded streaming messages; it does not contribute a file handler to `list-handlers` or draw any
objects yet. Add entities to the returned Scene to implement your format.

A `Classification` carries `score` (`Score`, any finite number) and `confidence` (`Confidence`, any finite number) separately; emit
each as the source provides it, or `None`.

Decoders may put any picklable debug data, in any shape, in `Scene.debug` (frame-level) or `Observation.debug`
(per object). For display behavior and ownership, see the [Scene Model invariants](../../../docs/domain/invariants.md#scene-model).

## Packaging requirements

- Give each distribution its own import namespace; avoid a shared top-level package named `plugins`.
- Declare every Python runtime requirement in `[project].dependencies`, for example `"protobuf>=5"`.
  Include `"ax-devil"` with any required version constraints. The installer supplies the editable app checkout
  in development, or pins the exact base app release for standalone installations. Plugins cannot upgrade the app.
- Declare supported Python versions honestly. The installation uses the Python version running ax-devil.
- Point an entry point at your plugin class or `PLUGIN_CLASS`. Declare `required_api_version()` explicitly. The host's
  version is `PLUGIN_API_VERSION` in `ax_devil.modules.plugin_system.base` (currently 3). A plugin built for an
  earlier version is rejected at load; there is no compatibility path, so rebuild it against the current version.
- Decoder plugins must declare `scene_model_version()` as `(major, minor)`. ax-devil exposes its own as
  `ax_devil.modules.scene.model.SCENE_MODEL_VERSION`. A plugin loads when the major matches and its minor is not
  newer. Breaking changes, including stricter requirements on Scene values, bump the major; additive fields bump the
  minor. Scene datetimes must be timezone-aware. Plugins that fail to load are listed in the Plugins view. Derived caches rebuild automatically when
  the Scene model version changes; bump the `artifact_version` argument to `SceneDecoderFileProvider` (see `decoders/mot/provider.py`) only when your
  own decoded output changes.
- Keep plugin IDs and handler types unique. Duplicate or incompatible registrations fail installation.
- For file decoders, override `file_to_scene_decoders()` and return `FileToSceneDecoderDefinition` objects. Set
  `file_extensions` (lowercase, such as `(".txt",)`) so the app can pick your decoder when it is the only one that reads
  a chosen overlay file; leaving it empty keeps the decoder available for every file.
- For playlist resolvers, use the `ax_devil.playlist_resolver_plugins` entry-point group and subclass
  `PlaylistResolverPlugin`. A workspace saves a resolver's id and settings and runs it again on every open, so:
  - `resolve(settings, token)` is required and headless: it turns a JSON-serializable settings dict (strings, numbers,
    lists, dicts; absolute paths) into `PlaylistContent`, and raises `ValueError` or `OSError` with a message for the
    user when settings are missing, invalid, or point at nothing. It runs on a background thread, so keep it in a
    module without Qt imports and free of shared mutable state. Poll the required `CancelToken` between filesystem
    operations and pass it through to helpers; callers without cancellation pass `NEVER` from
    `ax_devil.core.cancellation`. Let `Cancelled` escape so removed items and superseded resolutions stop quietly.
  - `source_paths(settings, token)` is optional and returns the `SourcePaths` (files and folders, from
    `ax_devil.core.source_paths`) the resolver reads, so a change to them resolves the playlist again; default empty.
    It receives the same cancel token as `resolve` and runs on the same background thread. The overlay files the
    returned `PlaylistContent` names are watched anyway.
  - `create_settings_widget()` is required: a `PlaylistResolverWidget` that edits settings and calls
    `submit_settings(settings)` when the user is ready. Slow work in the form, such as scanning a folder, goes through
    `self.jobs.submit(name, work, on_result=..., on_error=...)`: `work(job: JobContext)` runs on a worker thread and
    uses only the values it was given, and the callbacks run on the GUI thread until the form is closed. When the form
    closes, `work` stops at its next poll of `job.token` (`for path in job.token.polled(paths):`), which raises
    `Cancelled`, a `BaseException` that `except Exception` does not catch. Import `JobContext` (and `Cancelled`, to
    clean up) from `ax_devil.modules.plugin_system`.
    For saved-item editing, override the optional `restore_settings(settings) -> bool` hook described on
    [`PlaylistResolverWidget`](../../../src/ax_devil/modules/plugin_system/playlist_resolvers.py); the default supports adding only.
  - `create_cli_command()` is optional: it builds the same settings from CLI arguments and passes
    `[PlaylistItem(resolver=cls.plugin_id(), settings=settings)]` to
    `ctx.obj["run_with_items"]`.

  The model is `src/ax_devil/plugins/playlist_resolvers/folder_pair/` (`plugin.py`, `resolver.py`).
- External programs such as `protoc` are not Python dependencies; document their installation separately,
  or remove the runtime requirement by generating the needed files when building your package.

## Decoder classification filters

`FilterOption` accepts an Entity predicate for Scene filtering. To support whole-file object lists, declare a
classification policy using `make_classification_predicate(...).build_option(id=..., label=...)` or
`make_other_classification_predicate(...).build_option(...)` from `ax_devil.modules.filtering.predicate_utils`.
These policies match both real observations and the classification types recorded in object histories, including
unclassified objects via `include_empty=True`. List `UNMAPPED_CLASS` in a policy's types to match every `?<id>`
class. `ClassFilterSpec.build_option()` declares the same capability.

A config containing an Entity-only predicate supports frame filtering; its whole-file scope is disabled because
history does not contain geometry, confidence or other observation data needed to evaluate an arbitrary predicate.
