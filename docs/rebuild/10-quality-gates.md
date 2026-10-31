# 10 Quality Gates

Sources: `Makefile`, `pyproject.toml`, `.pre-commit-config.yaml`, `scripts/`, `.github/workflows/ci.yml`,
`.agents/skills/review/SKILL.md`, `AGENTS.md` "Done".

## Gates (MUST)

| Gate | Current | Requirement |
|------|---------|-------------|
| Formatter | `ruff format` | Standard formatter; check in CI, fix locally |
| Linter | `ruff check` | Standard linter with import ordering |
| Types | `mypy --strict` on src, tests, tools | Strictest practical checking, tests and tools included |
| Architecture | `scripts/check_architecture.py` | Automated dependency-rule check for [02](02-architecture.md#layers-and-dependency-direction) |
| Logging | `scripts/check_logging.py` | Enforce the logging rules in [03](03-runtime.md#logging-must) |
| File size | `scripts/check_file_tokens.py`: 5,000 tokens (bytes/4) per authored file | Same limit and exemptions, pre-commit and CI |
| Generated artifacts | Catalog JSON Schema and language reference generated from runtime definitions | One source of truth; CI fails on stale generated files |
| Dependencies | Bounded ranges; lowest-direct CI job | Same, plus license check ([13](13-security-packaging-ops.md#licensing)) |
| Fast mode | `make check-fast` | Changed-file mode for agents (SHOULD) |

Keep the command verbs so agents transfer: `sync`, `check`, `check-fast`, `format`, `test`, `test-failed`,
`test-integration`, `run`, `run-dev`.

## Definition of done

This list goes into the new `AGENTS.md` "Done" ([12](12-agent-setup.md#agentsmd)), its only home after the
playbook. It is achievable from the first UI slice: tokens, fit test and screenshot harness land in M0.

- Code, tests, dependencies or build config changed → `check` and `test` pass.
- Prose only → links, examples and consistency validated.
- Tests and docs follow `AGENTS.md` "Done": delete tests of removed code; doc additions need a stated reason.
- UI change → screenshots in both themes and two text sizes; fit test updated.
- Rendering change → instruction dumps and in-stack goldens reviewed; benchmark when on the hot path.
- Parity row → its line in the parity file updated with the test reference.

## Review

Port the review skill as the new review skill: deep modules, architectural cause, real problems with reachable
triggers, tests worth merging, docs at their layer. Findings carry file and line, trigger, consequence and fix.

## CI

Runs on PRs, `main` and release tags; required checks block merge; tag must match the package version; publish only
from tags after all jobs and the clean-environment install check pass.
