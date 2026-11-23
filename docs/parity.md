# Parity

One line per parity row of [01](rebuild/01-product-scope.md), which defines each row and its priority; a new feature
gets a row in 01 first. Each area keeps its rows and, beside them, where they differ from the reference:

| Rows | Area | Deviations |
|------|------|------------|
| WS, AC, LV | [Workspace, adding content, live](parity/workspace.md) | [workspace](parity/workspace-deviations.md) |
| OF, IN, RC | [Offline playback, inspection, render catalogs](parity/viewer.md) | [viewer](parity/viewer-deviations.md) |
| PL, ST, AP, SH, DT | [Plugins, settings, application, sharing, tooling](parity/app.md) | [app](parity/app-deviations.md) |

## Row format

- A row: `| ID | Feature | P | Status | Test | Notes |`. P is the row's priority in 01. Status: todo, in progress,
  done, dropped; dropping an M row needs the owner's written decision, recorded as a deviation (the check requires
  the deviation; review checks the decision).
- Test: backticked paths relative to the repository root, `crates/` or `frontend/src/`, where `{a,b}` (every
  alternative must exist) and `*` may stand for parts, or `make <target>`; text around them is free. A done row names
  at least one.
- A deviation: `| Row | Deviation | Reason |` in the deviations file of the area of the first row ID it names. Reference
  behavior IDs (three letters and digits, `VPC-009`, `LVV-A002`) may stand alone; put those rows where the feature
  lives (review checks that).

`scripts/check-docs.cjs` checks these rules in `make check-fast` and `make check`.
