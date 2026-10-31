---
name: verify-headless
description: Run ax-devil, a backend command, a test suite or the screenshot harness isolated from the user's files and desktop, on a private display, and inspect the result. Use whenever you need to see the app or a change working without touching the user's session, settings or data.
---

# Verify headless

Never use the user's desktop, focus, keyboard, mouse or screenshots of their screen, and never let a check write
their files. Everything runs through `scripts/isolated` ([testing runbook](../../../docs/runbooks/testing.md#tests-never-touch-the-users-files-or-session)).

## Loop

1. **Pick the narrowest check.** A focused suite first: `make tests-frontend TEST=<name>`,
   `make tests-backend TEST=<name>`. The desktop app and screenshots: `make tests-desktop`.
2. **Run anything else isolated.** Prefix the command with `scripts/isolated`, adding `--display` for anything that
   opens a window:

   ```bash
   make build
   scripts/isolated --display pnpm --dir desktop exec electron . --ozone-platform=x11 --smoke-test
   PORT=7400 scripts/isolated target/release/ax-devil   # backend alone, on a port no session uses
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
