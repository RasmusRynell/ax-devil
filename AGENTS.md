# AGENTS.md

## Who You Are Helping

Work out from the request which of two people you are helping; it decides what to read and what you may change.

- **Using ax-devil**: opening recordings, connecting cameras, comparing results, restyling overlays, or making
  ax-devil read their own data. Read `docs/usage.md`, `docs/plugins.md`, and the matching skill. Their catalogs and
  plugins live outside this repository; leave the app's source unchanged. If what they want needs an app change
  (a bug or missing feature), say so and ask before switching to development.
- **Developing ax-devil**: changing the app's code, tests, docs, or built-in plugins and catalogs. Follow everything
  below, read the docs under Read Next, and finish with the Done checks.

Plugins belong to both. A plugin for the user's own data is a separate package outside this repository. A plugin
shipped with ax-devil goes in `src/ax_devil/plugins/` and is development. The `write-plugin` skill covers both.

## Pre-Release Priority
- Optimize for the current code working correctly.
- Remove unused behavior and unsupported config shapes instead of carrying extra branches.
- Keep names, docs, comments, and tests aligned when shared terminology changes.

## Agent Skills

Task-specific instructions for any agent live in `.agents/skills/<name>/SKILL.md`; read the matching one before doing
that task. `.claude/skills` links to the same folder for Claude Code.

| Skill | Use it when |
|-------|-------------|
| `.agents/skills/review/SKILL.md` | Reviewing changes for real problems, module depth, architectural ownership, tests worth merging, and documentation placement |
| `.agents/skills/write-tests/SKILL.md` | Adding tests, writing regression coverage, changing assertions, or repairing flaky tests |
| `.agents/skills/render-catalog/SKILL.md` | Changing how overlays on video look: boxes, labels, colors, sizes, arrows, badges |
| `.agents/skills/write-ui/SKILL.md` | Adding or restyling application UI: windows, dialogs, panels, fonts, spacing, colors, text size |
| `.agents/skills/write-plugin/SKILL.md` | Writing a decoder or playlist resolver plugin, for the user's own data or to ship with ax-devil |

## Agent Requirements

- Every agent and subagent working in this repository must read this file before making project changes.
- Every agent and subagent that reads, edits, reviews, or generates Python code must follow the Python guidance in this file.
- Any Python code that does not follow the Python guidance in this file will be rejected without exception.
- All code generated in this repository will be evaluated by both a human reviewer and another AI agent.

## Python Guidance

### Baseline

- Target Python 3.10+.
- Use strict mypy.
- Use Ruff for linting and formatting.

### Python Commands

```bash
uv sync       # Create/update .venv with runtime + dev deps
make check    # ruff format --check + ruff check + mypy
make format   # ruff check --fix + ruff format
QT_QPA_PLATFORM=offscreen make test  # Regular offline pytest suite
QT_QPA_PLATFORM=offscreen make test-integration  # Run the real installation smoke test (may download packages)
```

### Code Rules

- Ruff enforces `E`, `F`, and `I` rules, a 120-character line limit, and double quotes.
- mypy strict mode applies: add type hints throughout and avoid `disallow_untyped_defs` violations.
- Use `from ax_devil.modules.settings.logging_config import get_logger` and `logger = get_logger(__name__)`. Do not use `print()`.
- Use f-strings only. Do not use string concatenation or `%` formatting.
- Add docstrings to public classes, methods, and functions.
- Add dependencies to `pyproject.toml` only when justified.
- Prefer minimal diffs. Do not refactor beyond what the task requires.

### Design Guidance

- Prefer simplicity over abstraction and clarity over cleverness.
- Before adding a special case, step back and ask whether a simpler, general design would handle it, and future cases like it, without branching.
- Prefer data structures that make the problem trivial over code that manages complexity.
- The best abstraction is often no abstraction — just the right data in the right collection.
- If multiple places need the same display or behavior decision, move that decision into the model instead of repeating conditional logic.
- For closed sets used across the app, prefer typed domain objects over raw strings when that removes duplicated branching.
- Treat `isinstance` as a code smell. When you find yourself routing by type, ask whether the object can answer the question itself, or whether a uniform collection such as a set or dict keyed by the object removes the branching entirely.

### Done

- For changes to code, tests, dependencies, or runtime/build configuration, run `make check` and
  `QT_QPA_PLATFORM=offscreen make test`.
- For prose-only documentation or agent-instruction changes, validate affected links, examples, and consistency,
  instead of running the application checks.
- Update tests when behavior changes.
- Before adding to a doc, apply the placement rule under Project Rules; most changes need no doc edit.

## Project Guidance

### Purpose

PySide6 desktop toolkit for inspecting Axis camera streams, offline video, and analytics overlays.

### Project Commands (interactive app launches are for the user)

```bash
make run        # Launch with INFO logging
make run-dev    # Launch with DEBUG logging
uv run ax-devil --help  # Every command; each has its own --help
```

### Project Rules

- Keep device credentials out of the repo — use `AX_DEVIL_TARGET_*` env vars or config overrides.
- Tests must never read or write the user's real files. The suite runs in its own home folder and fails if `~/.ax_devil` changes; a test that needs files uses `tmp_path` (see `docs/runbooks/testing.md`).
- Agent verification runs offscreen or on a private display. Never use the user's desktop, focus, keyboard, mouse, or desktop screenshots for testing. Follow `docs/runbooks/testing.md`; report checks that cannot run in isolation.
- When changing names, structure, or shared terminology, carry the change through the affected code, tests, comments, and docs, or explicitly note what is intentionally left unchanged. Unless the user says otherwise.
- Documentation follows the placement rule below. The default for a feature or fix is no documentation change.

### Where A Fact Belongs

Write down only what reading the code would not tell you: a rule, a boundary, a decision and its reason, or a
one-sentence summary of a large piece. Do not restate what a type, a test, or a docstring already says. The why of a
change goes in the commit message, not a doc.

A fact lives at the narrowest layer that covers every reader who needs it:

| Who must know it | Home |
|------------------|------|
| Code in two or more modules | `docs/architecture/` for how things fit together, `docs/domain/invariants.md` for rules that must stay true |
| One module | That module's `README.md` under `src/ax_devil/modules/` (create a short one if needed) |
| One class or function | Its docstring or a comment at the surprising line |
| Already enforced by a type, validation, or test | Nowhere |
| Someone using the app | `README.md`, `docs/usage.md`, `docs/settings.md`, `docs/plugins.md` |

Each fact has one home; link to it instead of repeating it. When a fact moves layers, delete it from the old home.

### Read Next

- Where code belongs: `docs/architecture/module-map.md`
- Rules for the area you change: `docs/domain/invariants.md`
- Terms: `CONTEXT.md`
- Architecture and data flows: `docs/architecture/overview.md`, `docs/architecture/ui-framework.md`
- Rendering and render catalogs: `docs/architecture/draw-system.md`, `docs/architecture/catalog-viewer.md`
- Testing workflow and patterns: `docs/runbooks/testing.md`
