# Rebuild Playbook

What the rewrite must build and how it must behave, taken from the Python app. What is done is in
[parity](../parity.md).

## Delivery

The rebuild is one branch in this repository, `rewrite`, and one pull request that merges to `main` once parity is
reached ([14](14-build-order.md#milestones)). The new app reads no data written by the old one: no importers, upgrade
paths or compatibility code.

To build a parity row, follow the `port-module` skill: the row's definition and Source in [01](01-product-scope.md),
then what 02 to 08 and 13 say about it. [14](14-build-order.md) gives the reason for a numbered decision (D19),
[15](15-open-decisions.md) the questions still open.

## Reference commit

`e31a7453695dfa2c649991efbfd35da9a74a238e`, the base of the rebuild branch.

- Every backticked repository path in this playbook (`docs/...`, `src/...`, `.agents/...`, `tests/...`, `tools/...`,
  root files) means that path at the reference commit, unless the text names it as a new-tree destination
  (`docs/parity.md`, the last column of Files). Short module paths (`video_viewer/README.md`) are relative to
  `src/ax_devil/modules/`. A `#anchor` after a path is a heading in that file.
- Read them with `git show e31a7453:<path>`, or check out a worktree:
  `git worktree add ../ax-devil-ref e31a7453`. The old app runs from that worktree when you need its behavior or
  reference outputs; nothing in the new tree depends on it.
- Markdown links point to files in this tree.
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

D17 decides whether this folder goes at parity. Before it does, each rule moves to the home in the last column (a
module README for rules one module keeps, the invariants for rules several keep, nothing when a type or test enforces
it).

| File | Owns | Home after the playbook |
|------|------|-------------------------|
| [01](01-product-scope.md) | Purpose, users, platform, parity rows (the only priority authority) | `docs/parity.md` until parity; scope sentences to `README.md` |
| [02](02-architecture.md) | Layers, dependency direction, seams to keep or redraw, deep modules | `docs/architecture/`, the architecture check |
| [03](03-runtime.md) | Concurrency, lifecycle, memory, time injection, error model, logging | `docs/architecture/overview.md`, invariants, module READMEs |
| [04](04-domain-rules.md) | How the reference's rules were reworded, dropped or deliberately changed; rules the code implies | The invariants |
| [05](05-formats-and-extensions.md) | Format policy, atomic writes, Scene schema, decoder and plugin contracts | Module READMEs, the `write-plugin` skill, `docs/plugins.md` |
| [06](06-ui-ux.md) | Principles, tokens, appearance, layout, states, focus, accessibility, HiDPI, overlay drawing | The `write-ui` skill, the kit README, invariants |
| [07](07-interactions.md) | Behavior that neither a row nor its source states | Tests and the parity files |
| [08](08-settings-cli.md) | Settings model and scopes, CLI surface, run modes | `crates/settings/README.md`, `docs/settings.md`, `--help` |
| [09](09-testing.md) | Test pyramid with the reference's examples, oracle policy | The `write-tests` skill, `fixtures/oracle/README.md` |
| [13](13-security-packaging-ops.md) | Credentials, network, sharing, plugin trust, packaging, licensing | `docs/settings.md`, `docs/plugins.md`, invariants, the testing runbook |
| [14](14-build-order.md) | D0, decisions taken, what is left | Nothing; each decision's rules live with their modules |
| [15](15-open-decisions.md) | Decisions the owner has yet to settle | Nothing |

## Ground rules

- **MUST** keep the vocabulary of `CONTEXT.md` in code, docs and UI.
- **MUST** preserve end use cases and consequential outcomes, not Python classes, APIs or implementation details.
  Architecture and interactions may improve; acceptance scenarios establish what users can accomplish. Reference
  docs and tests supply behavioral evidence, and deliberate workflow reductions stay explicit.
- **SHOULD** treat old tests as requirement candidates, filtered through the
  [write-tests skill](../../.agents/skills/write-tests/SKILL.md).
