# chrome module

Purpose: the shared look and frame of every window and dialog: theme and palette, design tokens, icons, custom
title bar and frameless window behavior, dialog base, form layout, scrolling pages and screen-aware geometry.
How to build UI with it is in the [write-ui skill](../../../../.agents/skills/write-ui/SKILL.md).

## Rules

- Sizes are never computed at import. Anything sized from text or colored from the palette runs through
  `appearance.follow_appearance`, which reruns it when the theme or text size changes.
- The video surface and its letterbox stay near-black (`theme.VIDEO_CANVAS`) in both themes, so they do not follow
  the palette and no Python event filter sees every frame they repaint. White playback controls belong on that
  scrim, not on a theme-dependent panel.
- Vertical content layouts keep rows at the top with a trailing `addStretch`, not `setAlignment(AlignTop)`: an
  aligned layout is sized by its size hint, ignores wrapped-text height, and overlaps rows when space is short.
- Do not give dialogs fixed or minimum opening sizes. `BaseDialog` opens at its content's preferred size, bounded to
  the screen and a comfortable maximum, and only the user resizes it afterwards; a dialog whose own controls change
  its content size declares `opening_size_hint` for its largest content. Content that changes with a selection lives
  in `QStackedWidget` pages built up front, so the preferred size already covers every choice.
- A dialog with its own scrolling view passes `scroll_content=False` and gives that view layout stretch; nested
  scrolling containers are avoided.
- Temporary modal dialogs use `with SomeDialog(...) as dialog:` so the result stays readable in scope and the dialog is
  deleted afterwards; `BaseDialog.done()` calls the idempotent `cleanup()` hook for every outcome.
- Main-window placement belongs to the desktop: `window_geometry.py` restores size and maximized state only, and never
  selects a screen or persists coordinates. Secondary windows open over their parent, bounded to the screen.
- Application icons come from the bundled Lucide set in `icons.py`, never Qt standard pixmaps or text glyphs.
