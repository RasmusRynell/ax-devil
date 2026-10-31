# Toolchain

Everything language-specific lives here; replace this file when the implementation language changes.

## Implementation

Python 3.10+ with PySide6 (Qt) for the UI. Dependencies and entry points are declared in `pyproject.toml`; add a
dependency only when justified. `make` targets are the stable interface for checks and tests.

## Install and launch

- From source: `uv sync --locked`, then `uv run ax-devil ...`. The launch command for a checkout is `uv run ax-devil`.
- Installed tool: `uv tool install ax-devil`, then `ax-devil ...`. `python -m pip install ax-devil` in a virtual
  environment also works.
- Development: `make run`, `make run-dev`, `uv run ax-devil --help`; `uv run pre-commit install` installs the Git hooks.

## Check and test

- `make check`: formatting, lint, logging, architecture and type checks. `make check-fast` checks changed files.
- `make format`: `ruff check --fix` and `ruff format`. `make test`: the offline pytest suite on a private Xvfb display.
  Focused runs: `uv run pytest tests/path/test_x.py::test_case -v`, `-k keyword`. `make test-integration` is the real
  installation smoke test.
- Ruff enforces `E`, `F`, `I` and some pyupgrade (`UP`) rules, a 120-character line limit and double quotes. mypy runs
  in strict mode.
- The suite imports nothing from the app before `tests/__init__.py` has set the temporary home and started Xvfb, because
  storage paths are computed at import time. Create applications through pytest-qt's `qapp`/`qtbot`, never during
  collection; register top-level widgets with `qtbot.addWidget()`; use `tmp_path` for files.
- UI screenshots: `PYTHONPATH=. AX_DEVIL_UI_SHOTS=<dir> uv run pytest -p tests.conftest tools/ui_screenshots.py -q`.

## Code rules

- Type hints throughout; docstrings on public classes, methods and functions.
- Log with `from ax_devil.modules.settings.logging_config import get_logger` and `logger = get_logger(__name__)`; no
  `print()`.
- f-strings only; no string concatenation or `%` formatting.
- Keep each file under 5,000 tokens (bytes / 4); the `file-token-limit` pre-commit hook rejects larger staged files.
- Treat `isinstance` as a code smell: let the object answer, or key a set or dict by the object.
- Automatic cyclic GC is disabled after startup (`app.py` freezes the heap and collects on a GUI-thread timer). Do not
  re-enable `gc`, call `gc.collect()` off the GUI thread, or change thresholds; the test suite does the same.

## Plugin packaging (current plugin API)

- A plugin is a Python distribution with a `pyproject.toml`. Its entry point sits in the `ax_devil.decoder_plugins` or
  `ax_devil.playlist_resolver_plugins` group and names the plugin class or a `PLUGIN_CLASS` module attribute.
- The host API is `PLUGIN_API_VERSION` in `ax_devil.modules.plugin_system.base` (currently 3); a plugin built for
  another version is rejected at load. Plugins declare the Scene version as `(major, minor)` against
  `ax_devil.modules.scene.model.SCENE_MODEL_VERSION`.
- Give each distribution its own import namespace (not a top-level `plugins`). List every Python runtime requirement
  in `[project].dependencies`, including `"ax-devil"` with any version constraint; the installer supplies the app
  and plugins cannot upgrade it. The installation uses the Python running ax-devil, so declare `requires-python`
  honestly. External programs such as `protoc` are not Python dependencies.
- `Cancelled` derives from `BaseException`, so `except Exception` does not swallow it. Let it escape; import it and
  `JobContext` from `ax_devil.modules.plugin_system`, and `NEVER` and `CancelToken` from `ax_devil.core.cancellation`.
- Data plugins hand to the host (`Observation.source_data`, `Observation.debug`, `Scene.debug`, playlist resolver
  settings) must be JSON-serializable. The derived caches currently store it with pickle, so it must also be picklable.
- Try a plugin before installing it: run a script with `uv run python` from the checkout and the plugin's package
  directory on `PYTHONPATH`.
- Lists of attributes decoders emit: `git grep -n 'Attribute(name=' src/ax_devil/plugins/decoders`.
