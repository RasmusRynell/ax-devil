# AGENTS.md

## Who You Are Helping

Work out from the request which of two people you are helping; it decides what to read and what you may change.

- **Using ax-devil**: opening recordings, connecting cameras, comparing results, restyling overlays, or making
  ax-devil read their own data. The rewrite has no user features yet ([parity](docs/parity.md)); user docs and the
  `render-catalog` and `write-plugin` skills return as their features land. Their catalogs and plugins live outside
  this repository; leave the app's source unchanged. If what they want needs an app change (a bug or missing
  feature), say so and ask before switching to development.
- **Developing ax-devil**: changing the app's code, tests, docs, or built-in plugins and catalogs. Follow everything
  below, read the docs under Read Next, and finish with the Done checks.

Plugins belong to both. A plugin for the user's own data is a separate package outside this repository. A plugin
shipped with ax-devil is development.

## Pre-Release Priority

- Optimize for the current code working correctly.
- Remove unused behavior and unsupported config shapes instead of carrying extra branches.
- Keep names, docs, comments, and tests aligned when shared terminology changes.

## Agent Skills

Task-specific instructions for any agent live in `.agents/skills/<name>/SKILL.md`; read the matching one before doing
that task. `.claude/skills` links to the same folder for Claude Code.
When writing or editing a skill or this file, the `writing-for-agents` and `write-a-skill` skills help if your agent
has them; they are optional and not part of this repository.

| Skill | Use it when |
|-------|-------------|
| `.agents/skills/review/SKILL.md` | Reviewing changes for real problems, module depth, architectural ownership, tests worth merging, and documentation placement |
| `.agents/skills/write-tests/SKILL.md` | Deciding whether a test is worth having, deleting tests, writing regression coverage, changing assertions, or repairing flaky tests |
| `.agents/skills/write-ui/SKILL.md` | Adding or restyling application UI: windows, dialogs, panels, fonts, spacing, colors, text size |
| `.agents/skills/port-module/SKILL.md` | Building one module or parity row of the rewrite from the reference commit |
| `.agents/skills/verify-headless/SKILL.md` | Running the app, a command or the screenshot harness in isolation and inspecting the result |

## Agent Requirements

- Every agent and subagent working in this repository must read this file before making project changes.
- Every agent and subagent that reads, edits, reviews, or generates code must follow the Rust and TypeScript
  guidance in this file.
- Code that does not follow the guidance in this file will be rejected without exception.
- All code generated in this repository will be evaluated by both a human reviewer and another AI agent.

## Rust and TypeScript Guidance

### Baseline

- Rust edition 2024 on the toolchain pinned in `rust-toolchain.toml`; one Cargo workspace.
- TypeScript in strict mode with React, built, linted, formatted and tested through Vite+ (`vp`).
- Node 22.18+, 24.11+ or 26+ and pnpm 12.14.0; one pnpm workspace with one root lockfile.

### Commands

```bash
make check       # Every static check, then every test suite
make check-fast  # Formatting, lint, types, architecture; file-size check on changed files; no tests
make format      # cargo fmt, Vite+ format and lint fixes
make tests       # Every suite on an isolated home; tests-frontend, -backend, -integration, -desktop run one
make desktop     # Launch the app with live reload (interactive launches are for the user)
make help        # Every target
```

Targets install what they need on first use; there is no setup step. Use the Make targets rather than parallel
entry points, and keep reusable build and test steps in the Makefile.

### Code Rules

- rustfmt and the Vite+ formatter own formatting; `cargo clippy -D warnings` with the workspace lints and the Vite+
  linter with type-aware rules must pass. Do not silence a lint without a comment saying why.
- Keep each file under 5,000 tokens (bytes / 4); `make check` rejects larger files and prints how to split them.
- App code never prints: no `println!`, `eprintln!` or `dbg!` in Rust, no `console` in the frontend (lints enforce
  it). Report through the UI or the logger.
- Build strings with `format!` and template literals, not concatenation.
- Give public Rust items `///` docs (the `missing_docs` lint enforces it) and exported TypeScript functions and
  components a `/** */` comment.
- Every `unsafe` block carries a `// SAFETY:` comment stating the invariant it relies on.
- Errors are values: return `Result` with a user-facing reason; `unwrap` and `expect` only in tests or where a
  comment proves the case impossible.
- Add a dependency only when justified; it must pass the license allow list in `deny.toml`.
- Prefer minimal diffs. Do not refactor beyond what the task requires.

### Design Guidance

- Prefer simplicity over abstraction and clarity over cleverness.
- Before adding a special case, step back and ask whether a simpler, general design would handle it, and future cases like it, without branching.
- Prefer data structures that make the problem trivial over code that manages complexity.
- The best abstraction is often no abstraction — just the right data in the right collection.
- If multiple places need the same display or behavior decision, move that decision into the model instead of repeating conditional logic.
- For closed sets used across the app, prefer typed domain objects (Rust enums, TypeScript string-literal unions) over raw strings when that removes duplicated branching.
- Treat switching on runtime types (`Any` downcasts, `instanceof`, matching on a type tag to pick behavior) as a code smell. When you find yourself routing by type, ask whether the object can answer the question itself (a trait method), or whether a uniform collection such as a set or map keyed by the object removes the branching entirely.

### Done

- For changes to code, tests, dependencies, or build configuration, run `make check`.
- For prose-only documentation or agent-instruction changes, validate affected links, examples, and consistency,
  instead of running the application checks.
- Tests: delete the tests of code you remove, merge or change before writing new ones. Add a test only for a deep
  module or a rule with real user consequences, once, at the layer that owns it (see the write-tests skill). A change
  that removes or merges code leaves test lines flat or lower; a net increase needs a stated reason.
- Docs: most changes need no doc edit. Before adding to a doc, apply the placement rule under Where A Fact Belongs and delete
  or collapse what the new text makes redundant. Net doc growth needs a stated reason.
- UI changes: screenshots in both themes from the harness (`make tests-desktop`), and, once text sizes and the fit
  test exist, at two text sizes with the fit test updated (write-ui skill).
- Rendering changes: Drawing Instruction dumps and in-stack goldens reviewed; a benchmark when on the hot path.
- A parity row: its line in [docs/parity.md](docs/parity.md) updated with the test that proves it.

## Project Guidance

### Purpose

Desktop app for inspecting Axis camera streams, offline video, and analytics overlays: a Rust backend, a React
frontend and an Electron shell.

### Project Commands (interactive app launches are for the user)

```bash
make desktop    # The app in an Electron window, debug backend, live reload
make web        # The same app in a browser at http://127.0.0.1:7331
make build      # Release build with the frontend embedded
```

### Project Rules

- Keep device credentials out of the repo — use `AX_DEVIL_TARGET_*` env vars or config overrides.
- Tests must never read or write the user's real files. Every suite runs through `scripts/isolated`, which gives it
  its own home and XDG folders and fails if the user's own ax-devil files change; a test that needs files uses its
  own temporary folder (see `docs/runbooks/testing.md`).
- Agent verification runs in isolation, GUI work on a private Xvfb display (`scripts/isolated --display`). Never use the
  user's desktop, focus, keyboard, mouse, or desktop screenshots for testing. Follow `docs/runbooks/testing.md`;
  report checks that cannot run in isolation. Use separate ports (`PORT`, `UI_PORT`) when a session may already be
  running, stop only processes you started, and put evidence in `artifacts/`.
- When changing names, structure, or shared terminology, carry the change through code, tests and comments. Update a doc only where it states a rule the change affects.
- Documentation follows the placement rule below. The default for a feature or fix is no documentation change.

### Where A Fact Belongs

Write down only what reading the code would not tell you: a rule, a boundary, a decision and its reason, or a
one-sentence summary of a large piece. Do not restate what a type, a test, or a docstring already says. The why of a
change goes in the commit message, not a doc.

A fact lives at the narrowest layer that covers every reader who needs it:

| Who must know it | Home |
|------------------|------|
| Code in two or more modules | `docs/architecture/` for how things fit together, `docs/domain/invariants.md` for rules that must stay true |
| One module | That module's `README.md` in its crate or frontend folder (create a short one if needed) |
| One type or function | Its doc comment or a comment at the surprising line |
| Already enforced by a type, validation, or test | Nowhere |
| Someone using the app | `README.md`, `docs/usage.md`, `docs/settings.md`, `docs/plugins.md`, `docs/datasets/`, only for a rule or a thing the UI and `--help` cannot show. Never list routes, menus, shortcuts, buttons, gestures or flags, or describe UI the user sees directly |

Each fact has one home; link to it instead of repeating it. When a fact moves layers, delete it from the old home.

### Read Next

- Where code belongs: `docs/architecture/module-map.md`
- Rules for the area you change: `docs/domain/invariants.md`
- Terms: `CONTEXT.md`
- Architecture and runtime: `docs/architecture/overview.md`
- Testing workflow and isolation: `docs/runbooks/testing.md`
- While the rewrite is in progress: `docs/rebuild/README.md` (what to build and how it must behave) and
  `docs/parity.md` (what is done)
