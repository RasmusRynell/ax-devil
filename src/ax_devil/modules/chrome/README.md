# chrome module

Purpose: the shared look and frame of every window and dialog: theme and palette, design tokens, icons, custom
title bar and frameless window behavior, dialog base, form layout, scrolling pages and screen-aware geometry.
How to build UI with it is in the [write-ui skill](../../../../.agents/skills/write-ui/SKILL.md).

## Rules

- Sizes are never computed at import. Code that sizes something from the text or picks a fixed palette color runs
  through `appearance.follow_appearance`, which reruns it when the theme or text size changes; widgets that only
  inherit the application font and palette, and icons drawn without a fixed color, follow on their own.
- `theme.VIDEO_CANVAS` is the near-black the [video surface keeps in both themes](../../../../docs/domain/invariants.md#ui-lifecycle);
  because it does not follow the palette, no Python event filter has to watch the frames it repaints.
- Vertical content layouts keep rows at the top with a trailing `addStretch`, not `setAlignment(AlignTop)`: an
  aligned layout is sized by its size hint, ignores wrapped-text height, and overlaps rows when space is short.
- Do not give a dialog its own fixed or minimum opening size. `BaseDialog` opens at its content's preferred size,
  bounded to the screen and a comfortable maximum, and only the user resizes it afterwards; a dialog whose own
  controls change its content size declares `opening_size_hint` for its largest content. Content that changes with a
  selection lives in `QStackedWidget` pages built up front, so the preferred size already covers every choice.
- A dialog with its own scrolling view passes `scroll_content=False` and gives that view layout stretch; nested
  scrolling containers are avoided.
- Temporary modal dialogs are opened with `with SomeDialog(...) as dialog:`, which deletes the dialog when the block
  ends; `done()` runs `cleanup()` but never schedules deletion, so a parented dialog opened without the block lives
  until its parent dies.
- Main-window placement belongs to the desktop: `ChromeWindow` restores size and maximized state only, and never
  selects a screen or persists coordinates.
- Application icons come from `icons.py`, never Qt standard pixmaps or text glyphs.
