# 12 Agent Setup and Skills

Owns: the agent-facing files of the new tree and the skills inventory. Docs layout and the day-one checklist:
[11](11-docs-structure.md).

## Agent-facing files at the reference commit

| File | Role | New tree |
|------|------|----------|
| `AGENTS.md` | Routing (user vs developer), language guidance, design guidance, Done, project rules, placement rule, Read Next | Rewrite as below |
| `.agents/skills/<name>/SKILL.md` | Task skills; `.agents/skills/write-plugin/reference.md` is disclosed reference | See inventory |
| `.claude/skills` | Symlink to `../.agents/skills` so Claude Code finds the same skills | Recreate the symlink |
| `CONTEXT.md` | Glossary agents use for names | Port |
| `Makefile` | Command verbs agents run | Same verbs in the new task runner ([10](10-quality-gates.md)) |
| `ax-devil --help` epilog | Links docs, AGENTS.md and skills | Keep |
| `tools/private_display.py`, `tools/ui_screenshots.py`, `ax-devil catalog check/render` | Headless verification | Rebuild (DT-01, DT-02, RC-04) |

No `CLAUDE.md` and no agent definitions exist in the repo; do not add them (AGENTS.md is the one entry point).

## AGENTS.md

Start from `AGENTS.md` at the reference commit. In "Python Guidance", rename the section to "<Language> Guidance"
and rewrite its Baseline, Commands and Code Rules subsections for the new stack. Keep its "Design Guidance" and
"Done" subsections: translate the `isinstance` line to the stack's equivalent (switching on runtime types) and merge
the definition of done from [10](10-quality-gates.md#definition-of-done) into Done. Keep every other section. Also:
the isolation rule points at [09](09-testing.md#isolation-must); Read Next adds `docs/rebuild/README.md` and
`docs/parity.md` while they exist; the skills table lists the inventory below.

## Skill structure

Structure every skill as the current ones do:

- Front matter with `name` and a `description` that names the trigger ("Use when ...").
- One `SKILL.md` under the file token limit (largest today: `render-catalog`, about 3,200 tokens); detail behind a
  pointer to a sibling file (`.agents/skills/write-plugin/reference.md` is the pattern).
- A loop with a verify step using exact headless commands and a report step saying what to tell the user.

Optional further guidance: the owner's user-global `writing-for-agents` and `write-a-skill` skills
(`~/.claude/skills/`, not part of this repo).

## Inventory

| Skill | Purpose and trigger | Carries over | Rewrite for the new stack |
|-------|---------------------|--------------|---------------------------|
| `review` | Review changes; working tree, commit or PR | Priorities, findings format, test and doc judgments | Links to the new module map, invariants, runbook |
| `write-tests` | Add, delete, repair tests | Delete-first, the four questions, choose-and-assert, flaky policy | Runbook links and fixture names |
| `write-ui` | Build or restyle application UI | Principles, button and accent conventions, fit rules, the check loop | Building-blocks table, code sample, screenshot commands (all Qt today) |
| `render-catalog` | Change how overlays look by editing catalog JSON | The whole loop and writing guidance (language kept) | Command names if the CLI changes; built-in catalog paths |
| `write-plugin` (with its `reference.md`) | Write a decoder or resolver for the user's data | The loop: real data, contract, package outside the repo, map to Scene, prove, install, hand over | Reference for the D1 runtime; Scene type names; install commands; example package |

New skills:

| Skill | Purpose | Level |
|-------|---------|-------|
| `port-module` | Port one module: read its invariants and README at the reference commit, list behaviors protected by old tests, write tests at the owner, implement, update its parity rows | SHOULD, day one |
| `verify-headless` | Run the app, a CLI command or the screenshot harness in the isolated environment and inspect output | SHOULD, day one |
| `add-feature` | Feature end to end after parity: parity row, owner module, tests, docs placement | Later, owner decision |

Delegation: concept-owned modules, module READMEs and parity IDs let one agent own one module with little context;
fast checks keep loops short. Real-device checks use test devices from environment variables, never the offline suite.
