---
name: write-ui
description: Build or change ax-devil's interface (windows, dialogs, panels, lists, buttons, fields, chips, spacing, colors, light/dark) in the React frontend. Use whenever adding or restyling application UI so it matches the rest of the app; overlays drawn on video are the render-catalog skill's.
---

# Writing ax-devil UI

One compact, restrained interface, one theme, readable in light and dark at any browser zoom up to 200%. A button is
a button: every control gets its look from the shared kit, never from classes written in feature code. Requirements:
[06](../../../docs/rebuild/06-ui-ux.md). Components project backend state and never own it or do file, network or
decode work ([UI Lifecycle](../../../docs/domain/invariants.md#ui-lifecycle)).

## I need a UI element

1. **A kit component fits:** use it (table below).
2. **None fits:** compose the constants of `frontend/src/kit/look.ts` and import one only for the element no component
   can render. Constants: `CONTROL` + `EDGE` (outlined button; `EDGE.default` is the Enter action), `CONTROL_IN` +
   `CHOSEN` / `CHECKED` (label around a hidden radio / checkbox), `QUIET` (chrome without an outline), `FIELD`,
   `FIELD_IN`, `PILL`, `SQUARE`, `TONE` (by `Tone`), `KIND` (by `Kind`).
3. **A shape recurs, or two features need it:** add a block to `frontend/src/kit/`, built from `look.ts`, with an entry
   in `Gallery.tsx` (`#kit-gallery`, captured in both themes by `desktop/smoke-gallery.cjs`). It is not done without
   the Gallery entry.

| Need | Use (`frontend/src/kit/` unless noted) |
|------|-----|
| Action; the Enter action; icon-only; compact beside a field | `Button` (`type="submit"` is the Enter action; `label` without children makes it icon-only; `size="small"`) |
| Menu opener; folder button after a path field | `MenuButton`, `BrowseButton` |
| Borderless icon in a bar, header or over video | `IconButton` (`onCanvas` over video) |
| Icon (Lucide SVG in `assets/icons/`, listed in `icons.ts`) | `Icon`; colour a kind with `KIND` |
| Status tag, clickable tag | `Chip` (`tone`, `onClick`) |
| Any-number choice; one-of-many; mode switch | `ToggleChip`, `ChoiceChips` in a `ChipRow`, `SegmentedControl` |
| Tabs over one panel | `Tabs` |
| Group heading (count, note, aside, mark, fold) | `SectionCaption` (`mark`: `muted`, `accent`, a `Tone` or a `Kind`) |
| Sidebar view: header, action, scrolling body | `ViewPanel` |
| Captioned action rows | `RowList` |
| Banner with actions | `shell/Banner.tsx`, its `actions` slot |
| Text input, drop-down, search | `TextField`, `Select`, `SearchField` |
| Checkbox, checkbox list, on/off applied at once | `Checkbox`, `CheckList`, `Switch` |
| Modal with OK / Cancel | `Dialog` |
| Question only a choice dismisses (Escape does nothing, Enter gives `defaultId`) | `ChoiceDialog` |
| Menu, context menu, free floating panel | `Menu`, `ContextMenu`, `Popup` |
| Panel of controls beside a button | `Popover` |
| Shortcut keys | `KeyChips` |
| Windows | Created by the shell (`desktop/main.cjs`); the frontend never opens one |

A control that handles keys the shortcuts also use (Space, Enter, arrows) wraps them in `ownKeys` (`kit/keys.ts`).

## Rules

- **Tokens only.** Sizes, spacing, radii, heights and colours come from `frontend/src/tokens.css` as Tailwind
  utilities (`bg-surface`, `text-muted`, `p-m`, `h-row`, `text-small`). No arbitrary values, inline styles or raw
  colours. Every colour is a light/dark pair; muted text has its own token, never reduced opacity. A fixed size comes
  from a text-size token so it grows with the zoom.
- **Restrained.** Outlined or quiet, nothing more: no heavier rings, shadows or extra decoration. Every control is one
  row high with a visible keyboard-focus ring and a muted disabled state.
- **Colour carries meaning, only:** accent marks the Enter action, chosen (solid) and checked (tint and check)
  controls, the current tab or row edge, and a focused field. Tones (`success`, `caution`, `danger`) mark status.
  Kind colours mark video, playlist, live and lane, the same on icons and caption marks everywhere. Video controls
  keep their white-on-scrim look; the canvas stays near-black in both themes.
- **Terse copy.** No taglines, helper text or secondary labels; fix the layout instead. Icons, not glyphs (`×`, `⋯`);
  icon-only controls carry a label. An ellipsis `…` means work in progress (`Loading…`) and appears in no command,
  button, tooltip or placeholder.
- **Keyboard first.** Every action is reachable by keyboard; Escape closes, Enter runs the default.
- **Fit.** Size from content with flex and grid. Each page opens without scrolling, a horizontal scroll bar or a
  squeezed field at the window minimum and 200% zoom. If it does not fit, restructure (own page, tab or group) when
  the window is dense, enlarge through content sizing when it has room. Scroll only content that grows with the
  user's data; long lists (sidebar trees, Objects, Events) are virtualized.
- **Pointer sharing.** Give an element that others should see the pointer on a stable `data-cursor-id` inside a
  `data-cursor-region`, named like its neighbours (`menu:<label>`, `row:<id>`, `tab:<item>`, `transport:<control>`,
  `share…`, `sessions:…`). Popups and dialogs are regions of their own and share no pointer.
- **A new user preference** is a row in the settings table
  ([08](../../../docs/rebuild/08-settings-cli.md#settings-model)), applied live when possible.

## Verify

Run in isolation per [testing](../../../docs/runbooks/testing.md#desktop-suite-and-screenshots). `make test-desktop`
writes `artifacts/screenshots/` in both themes; add your surface to `desktop/smoke.cjs` (or the Gallery) and its fit
check at the window minimum and 200%. Open the PNGs: nothing clipped, overlapping or unreadable, and the new UI
matches its neighbours. Test behaviour and cut-offs, not fonts, colours or paddings. `make check` finishes. Report
which screenshots you looked at and what a virtual display cannot show.
