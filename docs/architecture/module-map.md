# Module Map

Where code and files belong. Layers and the dependency rule are in the [overview](overview.md#layers).

| Path | Owns |
|------|------|
| `src/main.rs` | Backend startup and shutdown; prints the readiness line the shell and supervisor wait for |
| `src/lib.rs` | Backend routes and serving the embedded frontend |
| `crates/<name>/` | One owning concept per crate (Scene model, catalog compiler, workspace, sources, ...), with its layer declared |
| `crates/api-types/` | Rust types sent to the frontend; `export-types` writes them to `frontend/src/generated/api.ts` |
| `frontend/src/` | The UI. Components and their tests sit side by side |
| `desktop/` | The Electron shell: windows, backend lifetime, and the smoke test and screenshot harness (`smoke.cjs`) |
| `assets/` | Packaged built-in resources: render catalogs, icons, banner. The app reads them in place and never writes them |
| `fixtures/` | Committed test inputs with their provenance and license ([README](../../fixtures/README.md)) |
| `scripts/` | Development and verification support; the Makefile is the entry point |
| `docs/` | Docs by the placement rule in [AGENTS.md](../../AGENTS.md#where-a-fact-belongs) |

## Placement rules

- Organize code by the concept that owns it, not by technical kind, and mirror that ownership in tests.
- A new Rust crate goes in `crates/`, inherits the workspace edition, Rust version and lints, takes shared dependency
  versions from the root `Cargo.toml`, and declares its layer.
- Rust unit tests live in the owning module's `#[cfg(test)]` block; tests across a crate's public interface go in that
  crate's `tests/` folder. Frontend tests are `*.test.tsx` beside the component.
- Generated files are never edited by hand: `frontend/src/generated/` comes from `crates/api-types`. `frontend/dist/`
  and `target/` are build output.
- Add JavaScript dependencies with a filter, for example `pnpm --filter ax-devil-ui add <package>`, and commit the root
  `pnpm-lock.yaml` with the manifest. Every new Rust or JavaScript dependency passes the license allow list in
  `deny.toml`.
