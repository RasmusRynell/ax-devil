---
name: port-module
description: Build one module or parity row of the ax-devil rewrite from the Python reference commit — read what it must do, list the behaviors its old tests protect, write tests at the owner, implement, and update its parity line. Use when starting or continuing work on any parity row in docs/parity.md or any module the rebuild playbook names.
---

# Port a module

Port behavior, not code: the reference docs hold the rules, the Python holds Python and Qt workarounds. Follow
[AGENTS.md](../../../AGENTS.md) and the [write-tests skill](../write-tests/SKILL.md).

## Loop

1. **Scope.** Pick the parity rows in [docs/parity.md](../../../docs/parity.md) and read each row's definition and
   Source in [01](../../../docs/rebuild/01-product-scope.md). Find the module's layer and seams in
   [02](../../../docs/rebuild/02-architecture.md) and anything [03](../../../docs/rebuild/03-runtime.md)–
   [08](../../../docs/rebuild/08-settings-cli.md) says about it. Set the rows to `in progress`.
2. **Read the reference.** Read every Source path at the reference commit, without checking it out:
   `git show e31a7453:<path>`; short module paths are under `src/ax_devil/modules/`. Read the module's `README.md`
   and the matching sections of `docs/domain/invariants.md` there, then the new
   [invariants](../../../docs/domain/invariants.md), which win where they differ. To see old behavior, run the old app
   from a worktree (`git worktree add ../ax-devil-ref e31a7453`), isolated as its own runbook describes.
3. **List behaviors.** From the old tests of the module (`git show e31a7453:tests/...`), list the behaviors they
   protect, filtered through the write-tests skill: drop wiring, private names and routes reaching the same result.
   Each remaining behavior becomes a test at the new owner. Expectations come from the requirement, never from old
   output; a known old bug is never frozen in.
4. **Design the seam.** Name the crate or frontend folder per the [module map](../../../docs/architecture/module-map.md)
   and its layer. Keep the interface small. Stop and ask the owner when the playbook leaves a decision open
   ([14](../../../docs/rebuild/14-build-order.md#open-decisions)) or the reference contradicts the invariants.
5. **Test, then implement.** Write the tests, see them fail, implement until they pass. Wire time, scheduling and
   cancellation through their injected services.
6. **Place the rules.** Move each rule the module now enforces from `docs/rebuild/` to its new home
   ([11](../../../docs/rebuild/11-docs-structure.md#retiring-docsrebuild)): a module README for rules only it keeps,
   the invariants for rules other modules must keep, nothing when a type or test enforces it.
7. **Verify.** Run the focused suite, then `make check`. For UI, run the [verify-headless](../verify-headless/SKILL.md)
   loop and look at the screenshots.
8. **Record.** Update each row's line in `docs/parity.md`: status, the test that proves it, and any intentional
   deviation from the reference with its reason.

## Report

Tell the user which rows changed status, the behaviors covered and the tests that prove them, the decisions taken or
still open, deviations from the reference, the checks run, and what could not be verified in isolation.
