# 11 Docs Structure and Day-One Scaffold

Owns: the docs map of the new tree, the day-one scaffold checklist, what the rebuild PR deletes, and where this
playbook's content goes when `docs/rebuild/` retires. Agent files and skills: [12](12-agent-setup.md).

## Placement rule

Port the "Where A Fact Belongs" section of `AGENTS.md` unchanged into the new `AGENTS.md`; it is the one home of the
rule. In short: a fact lives at the narrowest layer that covers every reader who needs it, has one home, and is not
written down when code, types or tests already say it. The why of a change goes in the commit message. No ADR folder:
decisions with lasting rules go to the doc that owns the rule.

## Docs map

| Doc (new tree) | Owns | Readers | Old doc at reference commit | Action |
|----------------|------|---------|-----------------------------|--------|
| `README.md` | Pitch, install pointer, open-your-data examples, catalogs, plugins | Users first | `README.md` | Rewrite: commands and install change with the stack |
| `CHANGELOG.md` | Released and unreleased changes, known limits | Users | `CHANGELOG.md` | Rewrite: one Unreleased entry for the rebuild; keep limits that still hold |
| `LICENSE` | License | All | `LICENSE` | Keep unchanged |
| `CONTEXT.md` | Glossary | Developers, agents | `CONTEXT.md` | Port ([04](04-domain-rules.md)) |
| `AGENTS.md` | Agent routing, language guidance, Done, project rules, placement rule | Agents | `AGENTS.md` | Rewrite language guidance only ([12](12-agent-setup.md#agentsmd)) |
| `docs/domain/invariants.md` | Rules spanning modules that no type or test enforces | Developers, agents | same | Port ([04](04-domain-rules.md)) |
| `docs/architecture/overview.md` | Layers, data flows | Developers, agents | same | Rewrite from [02](02-architecture.md), [03](03-runtime.md) |
| `docs/architecture/module-map.md` | Where code belongs | Developers, agents | same | Rewrite for the new layout |
| `docs/architecture/workspace.md` | Workspace model, lifecycle, file format, resolver contract | Developers, agents | same | Port: mostly stack-neutral |
| `docs/architecture/draw-system.md` | Semantic/generic rendering boundary | Developers, agents | same | Rewrite: boundary kept, mechanisms change |
| `docs/architecture/ui-framework.md` | Shell composition and ownership | Developers, agents | same | Rewrite: toolkit-specific today |
| `docs/architecture/catalog-viewer.md` | Catalog window design rules | Developers, agents | same | Rewrite lightly when RC-03 lands |
| `docs/usage.md`, `docs/settings.md`, `docs/plugins.md` | User rules the UI and `--help` cannot show | Users | same | Rewrite: keep the rules, drop toolkit and Python details |
| `docs/installation.md` | Prerequisites, source build, packages | Users | same | Rewrite for D6 |
| `docs/datasets/*.md` | Dataset preparation, decoder behavior, attribution | Users | same | Port: content stays, commands change |
| `docs/runbooks/testing.md` | Commands, isolation, fixtures, CI | Developers, agents | same | Rewrite from [09](09-testing.md) |
| `docs/parity.md` | Parity tracker | Developers, agents | new | Create day one ([14](14-build-order.md#parity-tracking)) |
| Module `README.md` files | One module's rules | Developers, agents | 10 under `src/ax_devil/modules/` and `src/ax_devil/plugins/` | Recreate only where a new module has rules its code does not show |
| `tools/README.md` and tool READMEs | Developer tools | Developers, agents | `tools/README.md`, `tools/bench/README.md`, `tools/demo_recorder/README.md` | Rewrite with each tool |
| `docs/media/` | Demo images for README | Users | `demo-catalogs.webp`, `demo-compare.webp` | Drop; re-record with DT-06 |
| `docs/local/` | Personal, untracked notes | One developer | gitignored at reference commit | Keep the gitignore entry; committed docs never link it |

## Day-one scaffold

Done by the foundation PR on the prototype's stack, pending the owner's D0 signature
([14](14-build-order.md#d0-stack-choice)); step 6 covers the main window only until M0 adds text sizes and the fit
test. The scaffold, in this order:

1. Delete the old tree (list below); keep `LICENSE` and `docs/rebuild/`.
2. `AGENTS.md` for the new stack; `.agents/skills/` with `review`, `write-tests`, `write-ui` (kit table may start
   short), `port-module`, `verify-headless`; `.claude/skills` as a symlink to `../.agents/skills` ([12](12-agent-setup.md)).
3. `CONTEXT.md` and `docs/domain/invariants.md` ported; `docs/parity.md` with every row from [01](01-product-scope.md).
4. `docs/architecture/overview.md` and `module-map.md` for the new layers; `docs/runbooks/testing.md`.
5. Task-runner targets, gates, pre-commit hooks and CI ([10](10-quality-gates.md)).
6. The isolated GUI test environment and the screenshot harness (DT-01, DT-02).
7. Minimal `README.md` (what this is, build from source), fresh `CHANGELOG.md`, `.gitignore` for the stack plus
   `/docs/local/`.

Created as features land: `docs/usage.md`, `settings.md`, `plugins.md`, `installation.md`, `docs/datasets/`,
`docs/architecture/workspace.md` (M3), `draw-system.md` (M2), `ui-framework.md`, `catalog-viewer.md` (M6), module
READMEs, the `render-catalog` skill (M2), the `write-plugin` skill (M7).

## Deleted by the rebuild PR

`src/`, `tests/`, `tools/`, `scripts/`, `pyproject.toml`, `uv.lock`, `Makefile`, `.pre-commit-config.yaml`,
`.github/workflows/ci.yml`, `.agents/skills/*`, every doc above marked Rewrite, Port or Drop (recreated from scratch
where kept; `LICENSE` stays). Artifacts carried over by hand are listed in [migration.md](migration.md).

## Retiring docs/rebuild/

D17 decides whether `docs/rebuild/` is removed at parity or kept as history. Before removal, each rule must have its
new home:

| Playbook file | New home |
|---------------|----------|
| [01](01-product-scope.md) | `docs/parity.md` until parity; scope sentences to `README.md` |
| [02](02-architecture.md) | `docs/architecture/overview.md`, `module-map.md`, the architecture check config |
| [03](03-runtime.md) | `docs/architecture/overview.md` (runtime section), `docs/domain/invariants.md`, jobs module README |
| [04](04-domain-rules.md) | `CONTEXT.md`, `docs/domain/invariants.md` |
| [05](05-formats-and-extensions.md) | `docs/architecture/workspace.md`, `write-plugin` skill reference, `render-catalog` skill, `docs/plugins.md` |
| [06](06-ui-ux.md) | `write-ui` skill, component-kit module README, invariants (UI lifecycle) |
| [07](07-interactions.md) | Tests and the parity file; nothing else |
| [08](08-settings-cli.md) | Settings module README, `docs/settings.md` (rules only), `--help` texts |
| [09](09-testing.md) | `write-tests` skill, `docs/runbooks/testing.md` |
| [10](10-quality-gates.md) | `AGENTS.md` Done, `review` skill, task runner |
| 11 (this file) | `AGENTS.md` placement rule and Read Next |
| [12](12-agent-setup.md) | `AGENTS.md`, `.agents/skills/` |
| [13](13-security-packaging-ops.md) | `docs/settings.md`, `docs/plugins.md#trust`, invariants, testing runbook (release) |
| [14](14-build-order.md) | Nothing; decisions land in the docs that own their rules |
| [migration.md](migration.md) | Nothing; done when the PR carries the artifacts |
