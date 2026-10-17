---
name: write-tests
description: Decide whether and where an ax-devil test is worth having, delete tests that no longer earn their place, and write the few that protect meaningful behavior. Use when adding, deleting or changing tests, writing regression coverage, changing assertions, or repairing flaky tests in this repository.
---

# Write Tests

Follow [AGENTS.md](../../../AGENTS.md) and the [testing runbook](../../../docs/runbooks/testing.md), which owns
fixtures, isolation, Qt lifecycle rules and commands. Read the relevant
[domain invariants](../../../docs/domain/invariants.md) when establishing a requirement.

## Delete First, Then Decide

Delete the tests of code you removed, merged or changed before writing anything new. Do not re-point an
implementation-coupled test at a rename: delete it, and if a user-visible behavior is lost, cover it at its owner.
A change that removes or merges code leaves test lines flat or lower; a net increase needs a stated reason.

Add a test only for a deep module (small interface, real behavior behind it) or a rule with real user consequences,
once, at the layer that owns the decision. Do not test:

- different routes reaching the same method or result;
- wiring, private state or names, or production accessors added only for tests;
- a matrix of artificial states with no bug behind it;
- constructor echoes or deleted names.

## Establish What Matters

Before writing an assertion, answer: what supported action reaches this behavior, what would go wrong for the user if
it broke, what observation separates that failure from acceptable behavior, and would a harmless refactor break the
assertion?

Ground expectations in the request, an established contract, or a confirmed bug, not the current implementation;
clarify unclear intended behavior first. Strengthen an existing relevant test before adding another.

## Choose And Assert

Exercise the behavior through a stable interface at the lowest level that can expose the real failure: a pure
function, controller, component, or integrated workflow. Use widget interactions only for layout, focus, input or
rendering. Keep owned behavior real; fake external devices, transports and processes at their boundaries. Use small,
realistic inputs and relevant boundary cases.

Assert outcomes: correct frames and overlays, usable controls, preserved settings, valid output, released resources.
An internal observation is acceptable evidence for a real concern, such as a worker stopping or a cache avoiding a
decode, but never freezes incidental attributes or call sequences.

Distinguish usability from styling: a panel reaching exactly 280 pixels is not a requirement; test that controls stay
reachable, content is not clipped, or the layout recovers after resizing. Exact values are right only for a real
contract such as exported dimensions, frame identity or timestamp matching. Derive the expectation from the
requirement: do not copy today's output, import a production constant to follow the implementation, or add a
tolerance merely to turn a failure green.

## Make It Reliable

Follow the runbook's isolation and cleanup rules and reuse its fixtures. Wait for the observable state the assertion
needs, not arbitrary sleeps or an intermediate animation value, with bounded waits so broken behavior still fails.

For a flaky test, first decide whether its assertion protects a real requirement. If it does, tell a product defect
from a synchronization problem before repairing it; if not, delete it. Never just raise the timeout, relax the
comparison, or retry.

## Verify And Finish

Run the focused test first. For regression coverage, verify that it fails for the known defect and passes with the
fix when practical; otherwise explain the limit. For new behavior, check that a plausible wrong result would fail. Run the checks AGENTS.md requires. Report the behavior protected, the
tests deleted, the net test-line change (with a reason if it grew), the checks run, and any remaining limits.
