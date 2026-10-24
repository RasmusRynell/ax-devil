---
name: write-ui
description: Build or change ax-devil's Qt interface — windows, dialogs, panels, toolbars, lists, labels, buttons, painted widgets, spacing, fonts, colors, light/dark appearance, text size. Use whenever adding or restyling application UI so it matches the rest of the app; for overlays drawn on video, use render-catalog instead.
---

# Writing ax-devil UI

ax-devil aims for a slick, compact interface with little wasted space, readable in light and dark appearance and
at every **Text size** a user can choose; both theme and text size change live. New UI fits in by reusing the shared building blocks below instead of
choosing its own sizes and colors.

## The building blocks

| Need | Use | Where |
|------|-----|-------|
| Text size, weight, monospace | `TextRole` — `.apply(widget)` for widgets, `.font()` in painters and documents | `modules/chrome/tokens.py` |
| Margins, gaps, layout spacing | `Space` (2/4/8/12/16) | same |
| Corner radius | `Radius.CONTROL`, `Radius.POPUP` | same |
| Title bar, header, row, control height | `Height.<NAME>.px` (scales with text size) | same |
| Restyle on theme or text-size change | `follow_appearance(widget, apply)` | `modules/chrome/appearance.py` |
| Colors | `palette(...)` in stylesheets, `QPalette` roles when painting, `StatusColor` for success/warning/error | `modules/chrome/theme.py` |
| Palette color with alpha in a stylesheet | `palette_color_css` | `modules/chrome/palette_css.py` |
| Status-colored text | `StatusColor.<NAME>.css(palette)` | `modules/chrome/theme.py` |
| Icons | `Icon.<NAME>.icon()` follows the theme on its own; pass a color only for status or the video scrim. Add a Lucide SVG to `src/ax_devil/resources/icons/` for a new one | `modules/chrome/icons.py` |
| Action button | `QPushButton`, or `SmallButton` for compact rows; appearance comes from the shared theme | `modules/chrome/theme.py`, `small_button.py` |
| Button that opens a menu (`Filter ⌄`) | `MenuButton(text, menu)` | `modules/chrome/menu_button.py` |
| Folder button beside a path field | `BrowseButton(what, browse)` | `modules/chrome/browse_button.py` |
| Keyboard keys shown as chips (`[Ctrl] [N]`) | `KeyChips` widget, or `draw_key_chips` when painting | `modules/chrome/key_chips.py` |
| Top-level window | subclass `ChromeWindow` | `modules/chrome/chrome_window.py` |
| Dialog | subclass `BaseDialog`; add content with `add_content_widget` | `modules/chrome/base_dialog.py` |
| Pick one of two or three values in a form row or dialog | `ChoiceToggle(choices, current)`; `changed(value)`, `size_at(text_px)` | `modules/chrome/choice_toggle.py` |
| Question with a few labeled answers | `ask(...)`, never `QMessageBox` | `modules/chrome/choice_dialog.py` |
| Labeled input form | `FormLayout` | `modules/chrome/form_layout.py` |
| Scrolling page | `ContentScrollArea` | `modules/chrome/content_scroll_area.py` |

Before writing UI, read the [UI Lifecycle invariants](../../../docs/domain/invariants.md#ui-lifecycle) and the
[chrome README](../../../src/ax_devil/modules/chrome/README.md): they say when sizes may be computed, how to top-align
content, how dialogs get their size, and which pixels are exempt.

## Common patterns

```python
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Height, Space, TextRole

layout = QVBoxLayout(panel)
layout.setContentsMargins(Space.M, Space.M, Space.M, Space.M)
layout.setSpacing(Space.S)

title = QLabel("Entities")
TextRole.STRONG.apply(title)  # roles, never setPointSize / font-size literals; follows text-size changes

button = SmallButton("Rescan", Icon.SCAN)


def apply_appearance() -> None:  # anything sized from text, or colored from the palette in code
    header.setFixedHeight(Height.PANE_HEADER.px)
    warning.setPixmap(Icon.WARNING.icon(StatusColor.WARNING.color(header.palette())).pixmap(16, 16))


follow_appearance(header, apply_appearance)  # runs now, then on every theme or text-size change

TextRole.SMALL.apply(card_label)  # rich text takes its size and color from the label showing it
card_label.setText(f'<span style="font-weight:600;">{escape(name)}</span> {escape(value)}')
```

- **Body text needs nothing.** Widgets inherit the application font and palette and follow changes on their own,
  and so do icons from `Icon.<NAME>.icon()` without a color (`pin.setIcon(Icon.PIN.icon())`).
- **Action buttons follow Rescan:** neutral text and icons, transparent at rest, with a subtle neutral tint on hover
  and press. Main, secondary and default actions share this look; use the shared theme instead of local fills,
  outlines or primary variants. Default means the Enter-key action, not visual emphasis. Keep a visible keyboard-focus
  outline and muted disabled state. Blue accents identify checked controls, selected tabs and focused input borders;
  preserve checkbox hover feedback. Controls over video keep their contrasting white-on-scrim appearance.
- **Icons, not glyphs.** Use an `Icon` for buttons and markers instead of text characters such as `×`, `⋯` or `«`.
- **React through `follow_appearance`,** not a `changeEvent` override for `PaletteChange`.
- **Muted text uses `PlaceholderText`.** Dates, paths and section captions take that palette color, which stays readable
  in both themes; `Text` at a reduced alpha falls below readable contrast on the light theme.
- **An ellipsis means work in progress.** Write it as the one character `…`, as in `Loading…`; commands, buttons,
  tooltips and placeholders get none.
- **Pick the role by purpose.** Body text for content, `SMALL` for secondary labels, `CAPTION` for section headings
  (`.apply` adds its uppercase), `MONO` for frame numbers, IDs and times, `STRONG` for titles.
  `HEADING`/`DISPLAY` are rare.
- **Size containers from their content.** Prefer layouts and size hints over fixed widths; when a fixed size is
  unavoidable, derive it from `Height` or `TextRole.<ROLE>.px` so it grows with the text.
- **Added content must fit the window it joins.** Each page opens without scrolling, a horizontal scroll bar, tab
  scroll arrows, or a field squeezed below its `sizeHint()`; `BaseDialog` caps its opening size at 120 average
  characters by 48 lines. When it does not fit, take the more reasonable of two fixes:
  - **Restructure** when the window is already long or dense, or the addition is a concept of its own: give it its own
    page, tab or group, split a long page, or give a list editor full width instead of a form row.
  - **Enlarge** when the content is naturally wider and the window still has room under the cap, through size hints
    (such as `QTabWidget.setUsesScrollButtons(False)`), never a fixed size.

  Scrolling is right only for content that grows with the user's data, such as a list of rows.
- **A new user preference** follows `ThemeMode` and `TextSize` in `modules/settings/`: a `str` enum with
  `label` and `from_config`, a `GlobalSettings` property, and a row in the Settings dialog. Document it in
  `docs/settings.md` only for what its dialog text cannot show (a rule, a consequence, a security caveat). Apply it
  live when Qt allows; mark it `(requires restart)` only when it cannot; saving such a change offers to restart
  ax-devil.

## Check it

1. Take screenshots before and after the change, on a private display and isolated from the user's settings:
   `PYTHONPATH=. AX_DEVIL_UI_SHOTS=/tmp/ui-before uv run pytest -p tests.conftest tools/ui_screenshots.py -q`
   (then again with `/tmp/ui-after`). Add the surface you changed to `tools/ui_screenshots.py` if it is missing.
   Shots are drawn at 2 device pixels per logical pixel; set `AX_DEVIL_UI_SHOTS_SCALE=1` for smaller files.
2. Open the PNGs and compare dark and light, `medium` and `larger` text, wide and narrow windows. The change is done
   when nothing is clipped, overlapping or unreadable and the new UI matches its neighbours' sizes and spacing.
   To read one image instead of two, join a before and after shot with ImageMagick:
   `montage /tmp/ui-before/X.png /tmp/ui-after/X.png -tile 2x1 -geometry +4+0 /tmp/X-compare.png`.
   Also measure the fit, on a private display (`tools/private_display.py`) with `HOME` set to a temporary folder, at text sizes 13 and 21: show the window,
   and for each page compare its content's `sizeHint()` with the scroll area's viewport, check the scroll bars and
   tab bar, and compare each field's width with its `sizeHint()`. Numbers catch a page that only just scrolls.
3. Add the window or dialog to `SURFACES` in `tests/modules/chrome/test_surface_fit.py`, which checks that no button
   or label text is cut off in both themes at a small and a large text size. A guard test fails until a new
   `BaseDialog` or `ChromeWindow` subclass is there or in `EXEMPT` with the reason it is covered elsewhere.
4. Test behavior, and test cut-offs and overlaps that a size change could bring back. Leave fonts, colors and
   paddings untested; the screenshots cover them.
