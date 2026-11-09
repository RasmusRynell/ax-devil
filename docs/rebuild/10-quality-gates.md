# 10 Quality Gates

Sources: `Makefile`, `scripts/`, `.github/workflows/ci.yml`,
`.agents/skills/review/SKILL.md`, `AGENTS.md` "Done".

## Gates (MUST)

| Gate | Current | Requirement |
|------|---------|-------------|
| Formatter | `cargo fmt`, `vp fmt` | Standard formatter; check in CI, fix locally |
| Linter | `cargo clippy`, `vp lint` | Standard linter with import ordering |
| Types | `vp check` (strict TypeScript), rustc | Strictest practical checking, tests and tools included |
| Architecture | `scripts/check-architecture.cjs` | Automated dependency-rule check for [02](02-architecture.md#layers-and-dependency-direction) |
| Logging | Clippy and Oxlint lints | Enforce the logging rules in [03](03-runtime.md#logging-must) |
| File size | `scripts/check-file-tokens.cjs`: 5,000 tokens (bytes/4) per authored file | Same limit and exemptions, `make check-fast` and CI |
| Whitespace | `git diff --check` of the whole tree, `make check-fast` and CI | No trailing whitespace or blank line at end of file; `.gitattributes` `-whitespace` exempts fixtures and generated files |
| Generated artifacts | Catalog JSON Schema and language reference generated from the TypeScript catalog definitions | One source of truth; `make check` fails on stale generated files |
| Dependencies | Bounded ranges; lowest-direct CI job | Same, plus license check ([13](13-security-packaging-ops.md#licensing)) |
| Advisories | `make audit` (cargo-deny, `pnpm audit`, all dependencies because Electron ships from devDependencies), weekly in `.github/workflows/audit.yml` | Advisory only, outside `make check` and the PR gate: advisories change without a code change |
| Fast mode | `make check-fast` | Changed-file mode for agents (SHOULD) |

Agents need stable command verbs. The new tree keeps the prototype's Make verbs plus `check-fast` and `format`
([14](14-build-order.md#decisions-taken-for-the-foundation)); `AGENTS.md` lists them.

## Definition of done

This list goes into the new `AGENTS.md` "Done" ([12](12-agent-setup.md#agentsmd)), its only home after the
playbook. It is achievable from the first UI slice: tokens, fit test and screenshot harness land in M0.

- Code, tests, dependencies or build config changed → `check` and `test` pass.
- Prose only → links, examples and consistency validated.
- Tests and docs follow `AGENTS.md` "Done": delete tests of removed code; doc additions need a stated reason.
- UI change → screenshots in both themes at normal zoom and 200%; fit test updated.
- Rendering change → oracle comparisons and in-stack goldens reviewed; benchmark when on the hot path.
- Parity row → its line in the parity file updated with the test reference.

## Review

Port the review skill as the new review skill: deep modules, architectural cause, real problems with reachable
triggers, tests worth merging, docs at their layer. Findings carry file and line, trigger, consequence and fix.

## CI

Runs on PRs to and pushes on `main` and `rewrite`, and on release tags; required checks block merge; tag must match the package version; publish only
from tags after all jobs and the clean-environment install check pass. `make check` is the gate and CI runs it once
(`docs/runbooks/testing.md#ci`).
