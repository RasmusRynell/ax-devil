# 14 Build Order and Open Decisions

Delivery framing is in the [README](README.md#delivery). This file holds the stack criteria, D0, milestones and the
other decisions.

## Stack criteria

| Criterion | Ref |
|-----------|-----|
| Frame-accurate decode and seek, long-GOP H.264/H.265, 4K; Media Time from container timestamps, variable frame rate | [04](04-domain-rules.md#neutral-types-to-fix-early) |
| RTSP client exposing per-frame device capture time for video and metadata tracks (*current:* ONVIF replay header extension, requested with `onvifreplayext=1` in `data_sources/live/rtsp_source.py`; RTCP sender-report NTP mapping is the generic alternative); MQTT; WebSocket with Digest | LV-04, D14 |
| GPU video plus custom overlay drawing in one surface, and the same semantics offscreen at native size | [06](06-ui-ux.md#overlay-drawing) |
| Concurrency with a UI-thread hand-off and executor-bound destruction | [03](03-runtime.md#concurrency-guarantees-must) |
| Live theme and text-size changes, fractional HiDPI, accessibility API | [06](06-ui-ux.md) |
| Offscreen or private-display rendering, toolkit-level test input, window capture | [09](09-testing.md#isolation-must) |
| Strict typing, formatter, linter, dependency-rule checker | [10](10-quality-gates.md) |
| A plugin story compatible with D1 and D6 | [05](05-formats-and-extensions.md#plugin-runtime) |

## D0: stack choice

Before the deleting PR's scaffold, run throwaway spikes against the criteria: decode and seek, RTSP/ONVIF capture
time, offscreen rendering of video plus overlays, plugin bridge feasibility if D1 keeps a bridge, and the D4 schema
against both built-in resolvers. The owner signs D0 on the results. Spike code is not committed to the new tree.

## Milestones

Each exit is a commit on the rebuild branch with green CI and an updated `docs/parity.md`; the owner reviews the
diff range since the previous exit and signs it. Milestone listings schedule work; S rows enter an exit criterion only
when selected by the owner.

| M | Slice | Exit |
|---|-------|------|
| M0 | Day-one scaffold ([11](11-docs-structure.md#day-one-scaffold)); tokens; light/dark/auto and live text size; component kit seed; screenshot harness; fit-test guard | Empty window screenshots in both themes from CI; fit test passes |
| M1 | Skeleton: `local --video` through minimal item, Content, shared intake and resolution contracts into an in-memory Untitled workspace; clock-following playback, steps, seek; settings service; logging | Frame identity test; live theme and text-size switch passes the fit test |
| M2 | Overlay path: Scene model and serialization schema, decoder contract, one built-in decoder, overlay lookup and matching, catalog compiler with Standard, drawing preparation, `catalog check/render` | Canonical instruction comparison passes for Standard's sheets ([09](09-testing.md#oracle-policy)) |
| M3 | Workspace persistence, recovery copy, store, row projection, Workspace view, split view, `open` | Lifecycle tests and recovery-copy crash safety, toolkit-free |
| M4 | Offline depth: lanes (same and different videos), playlists, both resolvers, hide, frame cache, sticky, diagnostics, media tools, bookmarks, export | OF-02 parity case; alignment TSVs match the oracle; cache-build crash safety |
| M5 | Live: RTSP, ONVIF metadata, MQTT, DataHub, sync, status, scan, Live view | Fake-transport and burst tests; one manual camera session |
| M6 | Catalogs complete: six built-ins, manager, selection scopes, visibility, catalog window | Canonical comparison for all built-ins; catalog conformance suite ([09](09-testing.md#pyramid)) |
| M7 | Plugin runtime, all built-in decoders, external install/update/remove, Plugins view | The write-plugin example installs and decodes in a fresh process |
| M8 | Settings dialog, Quick Setup, Shortcuts dialog, accessibility pass, polish | Fit test for every window at both text-size extremes |
| M9 | Packaging and release; owner-selected S rows (benchmarks, release check, soak) | Every M row done or dropped by the owner in writing |

## Fixtures from the reference commit

Generated and committed as the oracle policy says ([09](09-testing.md#oracle-policy)); exports add timing references.

## Parity tracking

`docs/parity.md`: one line per row from [01](01-product-scope.md) with status (todo, in progress, done, dropped), test
reference and notes. Dropping an M row needs the owner's written decision. New features get a row in 01 first.

## Risks

| Risk | Mitigation |
|------|------------|
| Frame-accurate seeking and caching on long-GOP video | D0 spike; frame-identity and cache tests first |
| Overlay drawing cost | D0 spike; measurement against D9 in M2 |
| Matching and sticky semantics drift | Frame Identity rules as tests before code; oracle TSVs |
| Live transports have no equivalent in the new language (`ax-devil-rtsp`, `ax-devil-mqtt`, `ax-devil-device-api` in `pyproject.toml`) | D14 in the D0 spikes |

## Open decisions

The recommended default applies unless the owner objects. D5 and D16 are withdrawn; D13 became parity row AP-06.

| ID | Question | Recommended default | By |
|----|----------|---------------------|----|
| D0 | Language and UI stack | Owner signs after the spikes above | Before M0 |
| D1 | Plugin runtime; are unchanged third-party plugins supported (PL-05)? | Native built-ins; authors port third-party plugins to the new contract; a Python bridge is optional ([05](05-formats-and-extensions.md#plugin-runtime)) | D0 |
| D2 | Supported platforms? | Linux supported, including plugin management (Linux-only today); Windows and macOS build and pass the suite | D0 |
| D3 | One process or helper processes? | One process; next-entry warm-up may be a thread natively; a helper only for a plugin bridge | M1 |
| D4 | Resolver settings as a declared schema ([05](05-formats-and-extensions.md#resolver-contract))? | Yes | D0 |
| D6 | Packaging; bundled Python for plugins; GPL encoder in bundles? | AppImage or Flatpak plus source build; Python only if D1 keeps a bridge; non-GPL encoder in bundles | M2 |
| D7 | Custom frameless window? | Native frame default, custom frame optional | M8 |
| D8 | OS keychain for literal secrets in config and workspace items? | Yes, optional; `$VARIABLE` references stay default | M5 |
| D9 | Frame-time and memory budgets? | Proposals in [03](03-runtime.md#performance) on one named machine | M2 |
| D10 | Accessibility target? | WCAG 2.x AA contrast, full keyboard reach, accessible names | M0 |
| D11 | Localization? | English only; strings grouped per module | — |
| D12 | DataHub certificates (today: HTTPS/WSS without verification by default)? | Same for parity; pinning option later | M5 |
| D14 | Live transports in the new language? | Third-party RTSP/MQTT clients meeting the capture-time criterion plus a small Axis device-API module with temporary MQTT publisher setup and removal (LV-08) | D0 |
| D15 | Scene and Drawing Instruction dump script for the reference worktree? | Yes, throwaway; outputs committed with provenance | M2 |
| D17 | Remove `docs/rebuild/` at parity or keep as history? | Remove once every rule has its new home ([11](11-docs-structure.md#retiring-docsrebuild)) | M9 |
| D18 | When may workspace items expand `$VARIABLE` references ([13](13-security-packaging-ops.md#threat-model))? | Credentials bound to their host reference, else one confirmation showing the resolved host, remembered in user state. Behavior change from the reference | M3 |
