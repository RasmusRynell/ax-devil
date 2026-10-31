---
name: write-ui
description: Build or change ax-devil's interface — windows, dialogs, panels, toolbars, lists, labels, buttons, spacing, fonts, colors, light/dark appearance, text size — in the React frontend and the Electron shell. Use whenever adding or restyling application UI so it matches the rest of the app; for overlays drawn on video, the render-catalog skill covers them once rendering lands.
---

# Writing ax-devil UI

ax-devil aims for a slick, compact interface with little wasted space, readable in light and dark appearance and
at every **Text size** a user can choose; both theme and text size change live. New UI fits in by reusing the shared
building blocks below instead of choosing its own sizes and colors. The full UI requirements being ported are in
[06](../../../docs/rebuild/06-ui-ux.md).

## The building blocks

The component kit is built during the rewrite (M0 and later, [docs/parity.md](../../../docs/parity.md)). Add each
block to this table when it lands, and use what is listed instead of writing a local version.

| Need | Use | Where |
|------|-----|-------|
| Styling | Tailwind utility classes in components; global rules only in the base layer | `frontend/src/style.css` |
| Light and dark | System colors (`Canvas`, `CanvasText`) under `color-scheme: light dark`, following the OS live; theme tokens replace them in M0 | same |
| Icons | Lucide SVGs, one bundled set; add a new one from Lucide to `assets/icons/` | `assets/icons/` |
| Windows | Created by the shell; the frontend never opens windows itself | `desktop/main.cjs` |

Before writing UI, read the [UI Lifecycle invariants](../../../docs/domain/invariants.md#ui-lifecycle): components
project state owned by the backend, never own it, and never do file, network or decode work.

## Conventions

- **Tokens, never literals.** Once the token module exists, text sizes, spacing, radii, heights and colors come only
  from it; no pixel, rem or color literals in components. Text-derived sizes scale with **Text size**; margins do not.
- **Every color is a light/dark pair,** contrast-checked in both (target: WCAG AA). Muted text has its own token per
  theme, never text at reduced opacity, which fails contrast on light.
- **Action buttons are neutral:** neutral text and icons, transparent at rest, with a subtle neutral tint on hover
  and press. Main, secondary and default actions share this look; no local fills, outlines or primary variants.
  Default means the Enter-key action, not visual emphasis. Keep a visible keyboard-focus outline and a muted disabled
  state. The accent color marks only checked controls, selected tabs and focused input borders. Controls over video
  keep their white-on-scrim look; the video canvas stays near-black in both themes.
- **Icons, not glyphs.** Use an icon for buttons and markers instead of characters such as `×`, `⋯` or `«`; icon-only
  controls get an accessible name and a tooltip.
- **An ellipsis means work in progress.** Write it as the one character `…`, as in `Loading…`; commands, buttons,
  tooltips and placeholders get none.
- **No taglines, helper text or secondary labels.** If a screen needs them to make sense, fix the layout.
- **Keyboard first.** Every action is reachable by keyboard, focus is always visible, Escape closes popovers and
  Enter runs the default action.
- **Size containers from their content.** Prefer flex and grid layouts over fixed widths; when a fixed size is
  unavoidable, derive it from a text-size token so it grows with the text.
- **Added content must fit the window it joins.** Each page opens without scrolling, a horizontal scroll bar or a
  squeezed field at the largest text size and the window minimum. When it does not fit, take the more reasonable of:
  - **Restructure** when the window is already long or dense, or the addition is a concept of its own: its own page,
    tab or group, or a full-width list editor instead of a form row.
  - **Enlarge** when the content is naturally wider and the window still has room, through content sizing, never a
    fixed size.

  Scrolling is right only for content that grows with the user's data, such as a list of rows. Lists that can grow
  long (sidebar trees, Objects and Events) are virtualized.
- **A new user preference** is a row in the settings table ([08](../../../docs/rebuild/08-settings-cli.md#settings-model)),
  applied live when possible; only a setting that cannot apply live carries the restart marker.

## Check it

1. Take screenshots before and after the change, isolated from the user's desktop and settings: `make tests-desktop`
   writes `artifacts/screenshots/main-light.png` and `main-dark.png` (copy them aside between runs). Add the surface
   you changed to `desktop/smoke.cjs` if it is missing.
2. Open the PNGs and compare dark and light (and, once text size exists, the smallest and largest text size, wide and
   narrow windows). The change is done when nothing is clipped, overlapping or unreadable and the new UI matches its
   neighbours' sizes and spacing. To read one image instead of two, join them with ImageMagick:
   `montage before.png after.png -tile 2x1 -geometry +4+0 compare.png`.
3. Once the fit test exists (M0), add the window or dialog to it; it checks that no text or control is cut off in
   both themes at the smallest and largest text size, and a guard fails until every window is listed or exempted with
   a reason.
4. Test behavior (React Testing Library for components), and test cut-offs and overlaps that a size change could
   bring back. Leave fonts, colors and paddings untested; the screenshots cover them.
5. Report which screenshots you looked at and anything a virtual display cannot show (real GPU, compositor,
   fractional scaling on a real screen).
