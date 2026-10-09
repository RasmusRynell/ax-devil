---
name: write-tests
description: Add and improve ax-devil tests that protect meaningful behavior without coupling to incidental implementation choices. Use when adding tests, writing regression coverage, changing assertions, or repairing flaky tests in this repository.
---

# Write Tests

Follow [AGENTS.md](../../../AGENTS.md) and the [testing runbook](../../../docs/runbooks/testing.md).
The runbook owns fixtures, isolation, Qt lifecycle rules, and test commands. Read the relevant
[domain invariants](../../../docs/domain/invariants.md) when establishing a requirement.

## Establish What Matters

Before writing an assertion, answer:

1. What supported action or input reaches this behavior?
2. What would go wrong for the user if it broke?
3. What observation distinguishes that failure from acceptable behavior?
4. Would a harmless refactor or design change break the proposed assertion?

Ground expectations in the request, an established contract, or a confirmed bug, not merely the current
implementation. If the intended behavior is unclear and changes what counts as correct, clarify it first.
Do not invent requirements to justify coverage. Strengthen an existing relevant test before adding another;
omit or remove a redundant test that protects no meaningful behavior.

## Choose The Smallest Useful Test

Read the owning code and a neighboring test. Exercise the behavior through a stable interface at the lowest
level that can expose the real failure: a pure function, controller, component, or integrated workflow.
User-focused testing does not mean every test must drive the GUI end to end. Use widget interactions when
the concern is actual layout, focus, input, or rendering, rather than a decision owned by a controller.

Keep owned behavior real where practical; fake external devices, transports, and processes at their boundaries.
Do not mock away the behavior under test. Use small, realistic inputs and relevant boundary cases, not a large
matrix of artificial states. Scale coverage with the risk and affected contracts.

## Assert The Requirement

Assert outcomes such as correct frames and overlays, usable controls, preserved settings, valid output,
or released resources. Internal observations can be useful evidence for a real concern, such as a worker
stopping or a cache avoiding another decode, but should not freeze incidental attributes or call sequences.

For UI tests, distinguish usability from styling. A panel reaching exactly 280 pixels is not by itself a
user requirement. Depending on the actual bug, test that controls remain reachable, content is not
unintentionally clipped, or the layout recovers after resizing. Scrolling may be the intended way to keep
content accessible; do not impose a blanket rule against scrollbars.

Exact values are appropriate when they express a real contract: exported image dimensions, frame identity,
timestamp matching, or specified numeric output. Relationships or justified tolerances suit requirements
that allow variation. Do not copy today's output, import a production constant just to make an expectation
follow the implementation, or add a tolerance merely to turn a failure green. Derive the expectation
independently from the requirement; shared constants are fine when they genuinely define that contract.

## Make It Reliable

Follow the runbook's offline, temporary-storage, offscreen/private-display, and cleanup rules. Reuse existing
fixtures and helpers rather than introducing a test framework or exposing internals only for tests.
Wait for the completion or observable state relevant to the assertion, not arbitrary sleeps or a related
widget's intermediate animation value. Keep waits bounded so broken behavior still fails.

For a flaky test, first decide whether its assertion protects a real requirement. If it does, distinguish
a product defect from a synchronization problem before repairing it. If it does not, rewrite or remove it;
do not merely increase the timeout, relax the comparison, or retry until it passes.

## Verify And Finish

Run the focused test first. For regression coverage, verify that it fails for the known defect and passes
with the fix when practical; otherwise explain the verification limit. For new behavior, check that a
plausible wrong result would fail the assertions. A passing test alone does not establish useful coverage.

Run the checks required by AGENTS.md for test/code changes. Report the behavior protected, the checks run,
and any remaining limits. Keep test names about behavior and avoid adding fixtures, comments, or abstractions
that do not make the test easier to understand or maintain.