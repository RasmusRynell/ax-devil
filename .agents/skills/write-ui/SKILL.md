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
| Icons | `Icon.<NAME>.icon()` follows the theme on its own; pass a color only for status or the video scrim. Add a Lucide SVG to `resources/icons/` for a new one | `modules/chrome/icons.py` |
| Button that opens a menu (`Filter ⌄`) | `MenuButton(text, menu)` | `modules/chrome/menu_button.py` |
| Top-level window | subclass `ChromeWindow` | `modules/chrome/chrome_window.py` |
| Dialog | subclass `BaseDialog`; add content with `add_content_widget` | `modules/chrome/base_dialog.py` |
| Labeled input form | `FormLayout` | `modules/chrome/form_layout.py` |
| Scrolling page | `ContentScrollArea` | `modules/chrome/content_scroll_area.py` |

Before writing UI, read the [UI Lifecycle invariants](../../../docs/domain/invariants.md#ui-lifecycle): they say when
sizes may be computed, how to top-align content, which colors belong on the video scrim, and which pixels are exempt.

## Common patterns

```python
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole

layout = QVBoxLayout(panel)
layout.setContentsMargins(Space.M, Space.M, Space.M, Space.M)
layout.setSpacing(Space.S)

title = QLabel("Entities")
TextRole.STRONG.apply(title)  # roles, never setPointSize / font-size literals; follows text-size changes

button.setStyleSheet(f"""
    QToolButton {{
        border-radius: {Radius.CONTROL}px;
        padding: {Space.XS}px {Space.M}px;
        color: palette(text);
    }}
    QToolButton:hover {{ background: palette(midlight); }}
""")  # no font in stylesheets: it would override the role font


def apply_appearance() -> None:  # anything sized from text, or colored from the palette in code
    header.setFixedHeight(Height.PANE_HEADER.px)
    warning.setPixmap(Icon.WARNING.icon(StatusColor.WARNING.color(header.palette())).pixmap(16, 16))


follow_appearance(header, apply_appearance)  # runs now, then on every theme or text-size change

TextRole.SMALL.apply(card_label)  # rich text takes its size and color from the label showing it
card_label.setText(f'<span style="font-weight:600;">{escape(name)}</span> {escape(value)}')
```

- **Body text needs nothing.** Widgets inherit the application font and palette and follow changes on their own,
  and so do icons from `Icon.<NAME>.icon()` without a color (`pin.setIcon(Icon.PIN.icon())`).
- **Icons, not glyphs.** Use an `Icon` for buttons and markers instead of text characters such as `×`, `⋯` or `«`.
- **React through `follow_appearance`,** not a `changeEvent` override for `PaletteChange`.
- **Pick the role by purpose.** Body text for content, `SMALL` for secondary labels, `CAPTION` for section headings
  (`.apply` adds its uppercase), `MONO` for frame numbers, IDs and times, `STRONG` for titles.
  `HEADING`/`DISPLAY` are rare.
- **Size containers from their content.** Prefer layouts and size hints over fixed widths; when a fixed size is
  unavoidable, derive it from `Height` or `TextRole.<ROLE>.px` so it grows with the text.
- **A new user preference** follows `ThemeMode` and `TextSize` in `modules/settings/`: a `str` enum with
  `label` and `from_config`, a `GlobalSettings` property, a row in the Settings dialog, and a paragraph in
  `docs/settings.md`. Apply it live when Qt allows; mark it `(requires restart)` only when it cannot.

## Check it

1. Take screenshots before and after the change, offscreen and isolated from the user's settings:
   `PYTHONPATH=. AX_DEVIL_UI_SHOTS=/tmp/ui-before uv run pytest -p tests.conftest tools/ui_screenshots.py -q`
   (then again with `/tmp/ui-after`). Add the surface you changed to `tools/ui_screenshots.py` if it is missing.
2. Open the PNGs and compare dark and light, Default and Larger text, wide and narrow windows. The change is done
   when nothing is clipped, overlapping or unreadable and the new UI matches its neighbours' sizes and spacing.
3. Test behavior, and test cut-offs and overlaps that a size change could bring back. Leave fonts, colors and
   paddings untested; the screenshots cover them.
