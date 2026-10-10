---
name: review
description: Review ax-devil changes for real user problems, deep modules, root architectural fixes, behavior-focused tests worth merging, and layered documentation. Use for working-tree, commit, or PR reviews in this repository.
---

# Review ax-devil

Review against the requested behavior and `AGENTS.md`. This skill defines the repository's review workflow and
output; it takes precedence over a general review skill. Keep the review proportional to the change: separate
reviewer agents and ready-to-apply patches are optional, not required.

## Establish the change

Identify the task and review target. For working changes, include staged, unstaged, and relevant untracked files;
for committed changes, identify the base and head. Read affected callers and tests as well as the diff.
Inspection requests are review-only; apply fixes when requested or already authorized by the implementation task.

Read [the module map](../../../docs/architecture/module-map.md) when assessing ownership, relevant sections of
[domain invariants](../../../docs/domain/invariants.md) for affected behavior, and
[the testing runbook](../../../docs/runbooks/testing.md) when assessing or running tests.

## Review priorities

### 1. Deep modules

A deep module provides substantial behavior behind a small interface. Judge what callers must know, not file size
or class count. Follow actual callers: do they have to coordinate internal steps, duplicate policy, or understand
state that the module should own? Prefer an interface that hides that complexity and keeps related changes local.
Pass-through layers, exposed internals, and abstractions for hypothetical variants need a concrete benefit.

In ax-devil, widgets project state and emit intent; controllers and concept-owned modules own behavior. Shared
labels and decisions belong on their domain owner. Flag violations with the caller burden or duplicated knowledge
they create, rather than demanding abstractions merely to satisfy a design preference.

### 2. Fix the architectural cause

When a change adds a workaround, trace the problem to its owner. Prefer correcting that boundary or data model over
another conditional, duplicated state record, fallback, or widget-specific patch. Existing bad structure is not a
reason to extend it. Recommend the smallest complete correction, including affected callers, even if it touches
more files than a symptom fix. Favor removing unsupported behavior over preserving speculative compatibility.

Keep this tied to the task: identify how the existing design obstructs the requested behavior or creates concrete
maintenance costs in this change. Unrelated architectural cleanup belongs outside the review's required fixes.

### 3. Solve real problems

For each behavioral finding, establish a reachable trigger through supported app, CLI, plugin, or input paths and
explain the user impact. Prioritize actual playback, seeking, frame/overlay alignment, live connection handling,
workspace operations, persistence, export, and cleanup failures relevant to the change.

Trace validation and lifecycle guarantees before claiming an edge case is reachable. Artificial states created only
by bypassing those guarantees, unsupported configurations, and imagined future use cases are not priorities.
A rare but reachable crash, data-loss case, or shutdown race still matters; weigh evidence and impact, not frequency
alone. Distinguish confirmed issues from uncertainty, and omit speculation without a credible path to real use.

### 4. Merge tests that protect behavior

For every added or changed test, ask: **What real regression does this catch, and would a correct refactor keep it
passing?** Prefer simple, reliable tests that protect meaningful behavior and tolerate implementation changes.
Judge their coverage and maintenance cost; avoid complexity added solely to satisfy a testing convention.

Tests tied to incidental implementation details can help during development but should be removed or rewritten
before merging. Keep expectations independent of the implementation and verify that the test exercises the behavior
it claims to protect. Strengthen an existing meaningful case before adding redundant coverage.

### 5. Keep documentation at its layer

Apply the placement rule in [AGENTS.md](../../../AGENTS.md#where-a-fact-belongs). For every added or changed line
in `docs/architecture/` or `docs/domain/`, name the second module that must know it; if there is none, it belongs
in the module README, a docstring, or nowhere. User-facing docs (`README.md`, usage, settings, plugins) follow the
user instead: a fact belongs there when someone using the app needs it. Doc additions are rejected by default and
deletions accepted by default.

Treat `README.md` as the human entry point and review additions there especially carefully. For misplaced or
duplicated content, name the appropriate home or recommend deletion.

## Deliver the review

Report actionable findings in priority order. Each needs a file/line, concrete trigger or violated repository rule,
consequence, and proposed fix at the right owner. For a weak test, name the behavior it should protect or recommend
removing it. Avoid filler findings and generic design advice; a clean review is a valid result.

Validate the result against the current review target, and state which revisions and checks support your conclusion,
including their limits and remaining uncertainty. Use the testing runbook's isolated verification workflow. After
applying fixes, run the checks required by `AGENTS.md`. Review-only findings may describe the fix directly; provide a
patch when useful or requested.
