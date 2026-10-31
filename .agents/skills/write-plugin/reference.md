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
from ax_devil.modules.plugin_system import DecoderSpec, LineDecoderPlugin
from ax_devil.modules.scene.model import Scene, TimeSlice


class ExamplePlugin(LineDecoderPlugin):
    """Decode one frame number per text record, read from a file."""

    spec = DecoderSpec(
        id="example",
        name="Example decoder",
        handler="EXAMPLE_FRAME",
        api=3,
        scene=(3, 1),
        extensions=(".jsonl",),
        artifact_version=1,
        supports_sequence_lookup=True,
    )

    def decode(self, payload: str) -> Scene:
        """Accept a record such as '42'; the base handles bytes, blanks and whitespace."""
        frame = int(payload)
        return Scene(time_slice=TimeSlice(start=frame, end=frame))
```

Installation checks that the entry point imports and registers successfully. This example decodes text records from
files. `decode` must not depend on earlier calls: records are decoded in seek order by one reused instance. Add entities to the returned Scene to implement your format.

A `Classification` carries `score` (`Score`, any finite number) and `confidence` (`Confidence`, any finite number) separately; emit
each as the source provides it, or `None`.

Put values copied verbatim from the source in `Observation.source_data` and values your decoder computed in
`Observation.debug`; both are shown under Debug. `Scene.debug` is free-form frame diagnostics that are not shown. For display behavior and ownership, see the [Scene Model invariants](../../../docs/domain/invariants.md#scene-model).

## Packaging requirements

- Give each distribution its own import namespace; avoid a shared top-level package named `plugins`.
- Declare every Python runtime requirement in `[project].dependencies`, for example `"protobuf>=5"`.
  Include `"ax-devil"` with any required version constraints. The installer supplies the editable app checkout
  in development, or pins the exact base app release for standalone installations. Plugins cannot upgrade the app.
- Declare supported Python versions honestly. The installation uses the Python version running ax-devil.
- Point an entry point at your plugin class or `PLUGIN_CLASS`. Declare supported versions as literals in `DecoderSpec`
  (or the methods on `DecoderPlugin`). The host's API is `PLUGIN_API_VERSION` in `ax_devil.modules.plugin_system.base`
  (currently 3). A plugin built for an
  earlier version is rejected at load; there is no compatibility path, so rebuild it against the current version.
- Declare the Scene version as `(major, minor)`. ax-devil exposes its own as
  `ax_devil.modules.scene.model.SCENE_MODEL_VERSION`. A plugin loads when the major matches and its minor is not
  newer. Breaking changes, including stricter requirements on Scene values, bump the major; additive fields bump the
  minor. Scene datetimes must be timezone-aware. Plugins that fail to load are listed in the Plugins view. Derived caches rebuild automatically when
  the Scene model version changes; bump `artifact_version` only when your decoded output changes.
- Keep plugin IDs and handler types unique. Duplicate or incompatible registrations fail installation.
- Use `LineDecoderPlugin` for independent text records. Whole-file parsing, aggregation or non-text payloads use
  `DecoderPlugin` with explicit handler definitions (see `decoders/mot/`). Declare lowercase extensions so the app
  can select the sole matching decoder; empty means any file.
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
