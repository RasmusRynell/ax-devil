# 06 UI and UX

Sources: `.agents/skills/write-ui/SKILL.md` (component rules and the check loop), `chrome/README.md`,
`chrome/tokens.py`, `chrome/theme.py`, `docs/domain/invariants.md#ui-lifecycle`, `docs/architecture/ui-framework.md`.
Current feel: `docs/media/demo-catalogs.webp`, `docs/media/demo-compare.webp`.

## Principles (MUST)

- Compact: little wasted space, dense lists, the video gets the room.
- Readable in light and dark and at any browser zoom up to 200%; the theme switches live. Zoom is the browser's: no app text-size setting.
- No taglines, helper text or secondary labels; if a screen needs them, fix the layout.
- Every component comes from one shared kit; none picks its own sizes or colors.
- Shell layout: activity bar and sidebar, split viewer area, title bar with the application menu and window controls (`docs/architecture/ui-framework.md#top-level-composition`). SHOULD.

## Tokens (MUST)

Port the token sets by purpose; take values from `chrome/tokens.py`.

- Text roles (body, strong, small, caption, heading, display, mono) as offsets from one fixed body size; typeface
  follows the OS. Zoom scales everything together.
- One spacing scale, two radii, heights derived from text metrics.
- No literal sizes, spacing, radii or colors outside the token module.

## Appearance (MUST)

- Theme auto (follow the OS live), light, dark.
- Every color token is an explicit light/dark pair, contrast-checked in both.
- A dedicated muted-text token per theme, not derived by alpha. *Current:* Qt's `PlaceholderText` role, because text
  at reduced alpha failed contrast on light.
- Status colors (success, warning, error) and category accents distinguishable on both surfaces.
- The video canvas, letterbox and on-video controls stay near-black with white controls in both themes.
- Anything using a fixed color re-applies on theme change.
- Contrast target: D10 (proposal: WCAG 2.x AA, 4.5:1 text, 3:1 large text and component boundaries). The repo has
  no numeric target.

## Component kit

Rebuild the kit listed in the write-ui skill's building-blocks table (`.agents/skills/write-ui/SKILL.md#the-building-blocks`):
window and dialog frames, segmented toggle, toggle chips, popover, ask/notify dialog, form layout, browse button,
key chips, menu button, small button, sidebar view parts (panel, row list, caption, row, chip, banner), icons from
one bundled set. Port its conventions (neutral buttons, accent only for checked/selected/focused, `…` only for work in
progress, icons not glyphs) as the new write-ui skill ([12](12-agent-setup.md)).

## Layout and sizing (MUST)

- Dialogs open at their content's preferred size, bounded to the screen; content that varies with a selection is
  prebuilt so the preferred size covers it.
- Restructure (pages, tabs, groups) rather than scroll. Scrolling is for content that grows with user data, and as
  accessible overflow when screen bounds make fitting impossible.
- The main window restores size and maximized state only.
- Every control stays reachable and unclipped at the window minimum and at browser zoom up to 200% (fit test, [09](09-testing.md#ui-verification)).
- Details: write-ui skill "Common patterns" and `chrome/README.md#rules`.

## Large lists (MUST)

Sidebar trees and Objects/Events lists are virtualized and filter incrementally. Design for playlists with thousands
of entries and frames with thousands of objects; the long-playlist collapse (WS-14) is a view choice, not a
performance crutch.

## States

| State | Source |
|-------|--------|
| Empty workspace: start panel, welcome area with shortcut rows | `docs/architecture/ui-framework.md#workspace-shell` |
| Loading entry | `docs/architecture/overview.md#synchronization` |
| Pending and unavailable rows | `docs/domain/invariants.md#workspace-and-viewers` |
| Live connection status | `docs/architecture/overview.md#live-source-composition` |
| Out-of-date analysis | `video_viewer/README.md#rules` |
| Restart needed, recovered edits (banners) | `docs/usage.md#sidebar-views`, `docs/architecture/workspace.md#lifecycle` |
| Disabled action with a reason | `docs/architecture/ui-framework.md#sidebar-views` |

## Focus and keyboard (MUST)

- All shortcuts in one table with id, label, group, default key and flags; overrides only are persisted
  (`shortcuts/shortcuts.py`).
- Viewer shortcuts go to the focused viewer, also in lane fullscreen where flagged; media tools and zoom target the
  focused lane or the first lane (`video_viewer/README.md#rules`).
- Visible keyboard focus everywhere; Escape closes popovers; Enter runs the default action.
- Every action reachable by keyboard (D10; no audit exists).

## Accessibility and localization

- SHOULD: accessible names and tooltips on icon-only controls; respect reduced motion. *Current:* a few accessible
  names (`chrome/title_bar.py`), no screen-reader audit, English literals. Localization: D11.

## HiDPI (MUST)

- Crisp video, overlays and UI at fractional scales and at 2.
- Export at native source resolution whatever the desktop scale.
- Verify with a forced scale factor in the isolated test environment (`docs/runbooks/testing.md#quick-renderer-verification`).

## Overlay drawing

The rules are in `docs/domain/invariants.md#rendering` and `#coordinates`, `.agents/skills/render-catalog/SKILL.md`
and `video_player/README.md#quick-surface`. Additions:

- **MUST** keep display and export rendering semantically identical (same instructions, order, colors and geometry);
  pixels may differ only by documented renderer tolerances.
- **MUST** keep overlay pixels independent of app theme; they follow the frame.
- *Current mechanism:* one GPU quad stream plus a glyph atlas inside the toolkit scene graph. The requirement is crisp
  text at fixed screen size and per-frame cost within D9.
