---
name: verify-headless
description: Run ax-devil, a backend command, a test suite or the screenshot harness isolated from the user's files and desktop, on a private display, and inspect the result. Use whenever you need to see the app or a change working without touching the user's session, settings or data.
---

# Verify headless

Never use the user's desktop, focus, keyboard, mouse or screenshots of their screen, and never let a check write
their files. Everything runs through `scripts/isolated` ([testing runbook](../../../docs/runbooks/testing.md#tests-never-touch-the-users-files-or-session)).

## Loop

1. **Pick the narrowest check.** A focused suite first: `make test-frontend TEST=<name>`,
   `make test-backend TEST=<name>`. The desktop app and screenshots: `make test-desktop`.
2. **Run anything else isolated.** `ISOLATE=1` gives the release app (`make app`) a private home and display and the
   backend (`make cli`) a private home:

   ```bash
   make app ISOLATE=1 ARGS=--smoke-test
   make cli ISOLATE=1 PORT=7400   # backend alone, on a port no session uses
   ```

   Use your own `PORT` and `UI_PORT` when a session may already be running, and stop only processes you started.
3. **Inspect.** Open the screenshots in `artifacts/screenshots/` and compare them with what the change should show,
   in both themes. Put other evidence (logs, dumps, images) in `artifacts/` too.
4. **Extend the harness when a surface is missing.** Add the window or state to `desktop/smoke.cjs`, waiting for an
   observable state (an element, a computed style, a drawn frame) instead of sleeping.

## Report

Say which commands ran and their result, which screenshots you inspected and what they show, and what an isolated
virtual display cannot verify (physical GPU, compositor, window manager, fractional scaling on a real screen) as not
run.
