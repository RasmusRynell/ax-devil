# Rebuild Playbook

Guidance for re-implementing ax-devil in another language and UI stack: what to build, how it must behave, which
rules to keep.

## Delivery

The rebuild is one PR in this repository. After the owner signs the stack choice (D0), it deletes all existing code
and everything tied to it (source, tests, tooling config, skills, docs describing the old implementation) and starts
from scratch with this playbook. The new app reads no data written by the old one: no importers, upgrade paths or
compatibility code; artifacts are carried over by hand ([migration.md](migration.md)).

Reading order: this README → [14](14-build-order.md) (stack criteria, D0) → [11](11-docs-structure.md) (docs and
day-one scaffold) → [12](12-agent-setup.md) (agent files and skills) → [01](01-product-scope.md) → the files each
milestone's parity rows point to.

## Reference commit

`e31a7453695dfa2c649991efbfd35da9a74a238e`, the base of the rebuild branch.

- Every backticked repository path in this playbook (`docs/...`, `src/...`, `.agents/...`, `tests/...`, `tools/...`,
  root files) means that path at the reference commit, unless the text names it as a new-tree destination
  (`docs/parity.md`, the scaffold paths in [11](11-docs-structure.md)). Short module paths (`video_viewer/README.md`) are relative to
  `src/ax_devil/modules/`. A `#anchor` after a path is a heading in that file.
- Read them with `git show e31a7453:<path>`, or check out a worktree:
  `git worktree add ../ax-devil-ref e31a7453`. The old app runs from that worktree when you need its behavior or
  reference outputs; nothing in the new tree depends on it.
- Markdown links in this playbook point only to other files in `docs/rebuild/`.
- Upstream changes after the reference commit enter the rebuild only as new parity rows in [01](01-product-scope.md).

When this playbook and the reference commit disagree on behavior, `docs/domain/invariants.md` wins unless a row here
marks the rule as deliberately changed; raise the conflict instead of guessing.

## Legend

| Mark | Meaning |
|------|---------|
| **MUST** | Constraint that holds whenever the feature exists. Changing it is an owner decision. |
| **SHOULD** | Default; deviate with a stated reason. |
| **M / S** | Delivery priority, set only by the parity rows in [01](01-product-scope.md). |
| **keep / change / drop** | Status of a current seam or mechanism. |
| *current: X* | Reference mechanism at the reference commit. A pointer, not a requirement. |

## Files

| File | Owns |
|------|------|
| [11-docs-structure.md](11-docs-structure.md) | Docs map, placement rule, day-one scaffold, what the PR deletes, playbook retirement |
| [12-agent-setup.md](12-agent-setup.md) | Agent-facing files, AGENTS.md deltas, skills inventory |
| [01-product-scope.md](01-product-scope.md) | Purpose, users, platform, parity rows (the only priority authority) |
| [02-architecture.md](02-architecture.md) | Layers, dependency direction, seams to keep/change/drop, deep modules |
| [03-runtime.md](03-runtime.md) | Concurrency guarantees, lifecycle, memory, time injection, error model, logging |
| [04-domain-rules.md](04-domain-rules.md) | Porting the glossary and invariants: translations, drops, deliberate changes |
| [05-formats-and-extensions.md](05-formats-and-extensions.md) | Format policy, atomic writes, Scene schema, decoder and plugin contracts |
| [06-ui-ux.md](06-ui-ux.md) | Principles, tokens, appearance, layout, states, focus, accessibility, HiDPI, overlay drawing |
| [07-interactions.md](07-interactions.md) | User flows mapped to parity rows and sources |
| [08-settings-cli.md](08-settings-cli.md) | Settings model and scopes, CLI surface, run modes |
| [09-testing.md](09-testing.md) | Test pyramid, isolation, oracle policy, reliability |
| [10-quality-gates.md](10-quality-gates.md) | Gates, definition of done, review, CI |
| [13-security-packaging-ops.md](13-security-packaging-ops.md) | Credentials, network, plugin trust, packaging, licensing |
| [14-build-order.md](14-build-order.md) | Delivery, stack criteria, milestones, fixtures, risks, open decisions |
| [migration.md](migration.md) | Artifacts to carry over by hand |

## Ground rules

- **MUST** keep the vocabulary of `CONTEXT.md` in code, docs and UI.
- **MUST** port behavior from the referenced docs and tests, not by translating implementation line by line; the docs
  hold the rules, the code holds Python and Qt workarounds.
- **SHOULD** treat old tests as requirement candidates, filtered through [09](09-testing.md#philosophy-must).
