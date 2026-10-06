# Catalog Viewer And Agent Authoring

Render catalogs (see [Draw System](draw-system.md)) decide how every overlay is drawn. People no longer edit them in
the app: they say what they want to an AI agent, the agent edits the catalog JSON following the repo skill, and the
**catalog viewer** shows the result the moment the file is saved.

## Handoff

- **Branch:** `catalog-editor` (the name predates the change of direction).
- **Try it:** `uv run ax-devil catalog` starts the app with the viewer open; it is also under **View → Render
  Catalogs** (`Ctrl+R`) and **View** next to a view's catalog choice. Make a catalog with **New copy…**, edit its file in any
  editor or ask an agent to, and watch it redraw.
- **Verified:** `make check` and `make test`, including the viewer, file-watching and CLI tests. The viewer was
  reviewed in the running app and as screenshots at 1240×760 and 1440×860. Tests run in their own home folder and fail
  the run if the user's ax-devil folders change (see [Testing](../runbooks/testing.md)).
- **Not verified:** file watching on Windows and macOS and on network file systems (the **Reload** button in each view
  remains as a fallback); **New copy…** and **Delete…** clicked by hand in the running app; an agent session driven
  only by the skill on a real request.
- **Where to start:** `src/ax_devil/modules/catalog_viewer/` (`sheets.py` for the examples, `street.py` and
  `footage.py` for what they are drawn over, `window.py` for the viewer, `cli.py` for the commands), the skill in
  `.agents/skills/render-catalog/SKILL.md`, and file watching in
  `SceneRenderCatalogManager` (`scene/rendering/catalog_manager.py`).

## Goal

Changing how overlays look should take one sentence from the user and no knowledge of the JSON:

1. The user describes the change ("make the ids bigger on everything, and draw people in orange").
2. An agent follows `.agents/skills/render-catalog/SKILL.md`: it finds the catalog in use, makes the smallest change
   at the right level (a component default for "everything", a recipe input for one type), checks it and looks at
   rendered examples.
3. The user watches the viewer, which redraws on save, and open video views pick the change up too.

## Pieces

The loader reads each catalog revision once and returns its source document together with the compiled catalog.
The manager caches that pair for both selections and the viewer. Example sheets always use the document that was
compiled, including when an editor saves again during loading; a failed reload keeps that file's last good preview.
Switching to a file that does not load clears the previous file's preview and description, including while hidden.

| Piece | What it does |
|---|---|
| File watching (`SceneRenderCatalogManager`) | Watches every listed catalog file, invalid ones included, and the directories holding them. After 150 ms of quiet it compares file fingerprints, refreshes the listing and emits `catalogFileChanged(path)` for each changed, replaced or removed file. Editors that save by replacing the file are followed. |
| Selections | A selection whose active path changed loads the new version; a version that does not load keeps the last one in use and reports the error, as any failed load does. |
| `sheets.py` | Builds example sheets from the catalog document, over footage-like backgrounds: **Overview** (one object of every type and one person-and-head pair of every relation, captioned, on an empty sunny street), **Street** (a camera view of a sunny street: people near and far with their heads and `has_part` relations, cars, a bus, a truck, a bike and unclassified motion, with overlaps, occlusion, shade and the frame edge; each detection is classified by its real class and routed by the catalog, so a catalog with few types falls back to its general ones), a sheet per object type (4×4 situations: tiny to large, confidence 15–98%, a long UUID id, still to moving fast and unknown movement, outline polygon, wide, overlapping, at the frame edge; the cells sit on bright concrete, foliage, asphalt and night tiles, each kind once per row and column), a sheet per relation (three sizes) and **Crowd** (48 objects on the street at night). Examples carry realistic data: track ids mix UUIDs (most common), small and large counters, `track-000412`, `obj_17_cam2` and short hex, and are unique in each sheet; every attribute a type binds gets a plausible value (weighted clothing and vehicle colors with falling scores, flags such as `carries_bag` set about a third of the time, `face_visible` mostly near 0 or 1). Ids and values come from generators seeded per sheet, so sheets look the same every time. Captions sit on dark plates so they read over bright ground. Rebuilding every sheet takes about 2 ms. `drawing_errors()` draws a sheet off screen and returns what drawing reported. |
| `street.py` | The street the **Street** and **Crowd** sheets show: frame size, perspective (`HORIZON`, `metre`, `lane`, `standing`) and `street_actors()`, the people, heads, vehicles and unclassified motion on it with the detection data a decoder would report. |
| `footage.py` | The backgrounds, painted with QPainter from fixed seeds once per process (about 50 ms): the `GRID_SIZE`×`GRID_SIZE` grid of bright, busy, grey and dark ground, and the street by day with or without its actors' silhouettes and at night. |
| `window.py` | `CatalogViewerWindow`: catalog choice, **New copy…**, **Delete…**, **Apply to all** (every open view) and **Use as default** (new views), file path, "● Live" (with the time of the last change) or "● Does not load", an error banner over the last version that loaded, a tab per sheet, the drawing and the sheet's description or drawing errors. It opens on the default catalog and follows a new default. It redraws only when visible and when the catalog or tab changed. `show_catalog_viewer()` keeps one viewer per manager. |
| `cli.py` | `ax-devil catalog` alone starts the app with the viewer open; `ax-devil catalog list`, `check [FILE]`, `render [FILE] --out DIR [--sheet KEY]`, `new NAME [--from FILE]`, `use FILE` and `reference`. `render` writes exactly the sheets the viewer shows, so an agent can look at its work. |
| `reference.py` | The language reference `ax-devil catalog reference` prints, generated from the runtime definitions: types with bounds, operation signatures, primitives, recipe and template inputs, bindings and limits. |
| The skill | `.agents/skills/render-catalog/SKILL.md`: the loop (find, render before, edit, check, render after, report), which file to edit, how catalogs are built, and how to write changes well. |

## Decisions

- **2026-09-29: An agent edits the JSON; the app only shows it.** After three rounds on an in-app editor (navigator,
  inspector, typed slots, a shared-style page, click-to-select in the preview), the user stopped it: agents can make
  these changes from a sentence, so the app needs a strong live viewer, and the repo needs a skill that makes agents
  write the JSON well. The editor was committed as a checkpoint (`ac82973`) and then removed with its tests, smoke
  tool and renderer hooks.
- **2026-09-29: The manager watches catalog files.** With files edited outside the app, open views must update without
  a save path in the app, so watching replaced the editor's `save_catalog()` and `catalogSaved`.
- **2026-09-29: Sheets are captioned situations, not a scene.** Each cell names what it shows, so a user can judge a
  style at every size, confidence and movement at a glance, and an agent can check the same images.
- **2026-10-03: Sheets look like real footage and real data.** The dark grid and ids "1", "2", "3" hid what goes
  wrong in production: text that vanishes over bright concrete or sky, labels lost in foliage, 36-character UUIDs,
  attributes that are not neatly alternating. Sheets now draw over procedurally painted bright, busy, grey and dark
  ground, add a **Street** camera view, and use realistic ids and attribute values, so weak designs show in the viewer.
  The situation grid stays captioned; Street has no captions because it shows a whole scene.
- **2026-09-29: The language reference is generated.** The skill points at `ax-devil catalog reference` instead of
  copying names and types, so it cannot fall out of date.
- **2026-09-29: Captions are text, not pixels.** Captions were first painted into the example image and turned
  blurry and broken whenever the viewer showed the frame smaller than 1280×720. They are now drawn like overlay text at
  a fixed screen size, so they stay sharp at every window size and zoom, in the viewer and in `render` output.
- **2026-09-29: Skills are for every agent.** The skill lives in `.agents/skills/` and is listed in `AGENTS.md`;
  `.claude/skills` only links to that folder.
- **2026-09-29: Users create and delete catalogs in the viewer.** **New copy…** starts a named catalog from the one
  shown, for an agent to edit; **Delete…** asks, then removes a user catalog file, including one that does not load.
  Built-in catalogs cannot be deleted, and a catalog that does not load cannot be copied. A deleted default catalog
  makes the default built-in one (Standard) the default.
- **2026-09-29: A new default is followed.** `ax-devil catalog use` lets an agent that copied the built-in catalog
  make the copy the default, and the viewer switches to it, so the user sees the change without doing anything. The
  viewer follows the *chosen* default (`SceneRenderCatalogListing.chosen_default_path`): when that file stops loading,
  views fall back to the built-in catalog, but the viewer and `ax-devil catalog check` keep showing the broken file
  and its error.
- **2026-09-29: The main window closes the viewer.** `close_catalog_viewer()` runs from `MainWindow.closeEvent`, so
  the viewer releases its renderer before the window that owns it is destroyed.
- **2026-10-03: Five built-in catalogs.** Standard (the default), Minimal, Chunky and Glass ship next to the original
  catalog, renamed Classic. All are read-only and listed first; users copy one to make their own. A built-in other than
  Standard is remembered as the default as `built-in:<file name>`.
- **2026-10-04: Tracking catalog.** A sixth built-in, Tracking, colors each object by its id with the new
  `pick_color` operation instead of by class, for debugging tracking.
- **Kept from the editor work:** value bounds on types and parameters, `label` and `visible` on recipes, templates,
  parameters and steps, mixed integer and number list literals widening to numbers, and the built-in catalog's
  promoted component parameters (line width, fill opacity, label color and lengths, bar colors and sizes, badge color)
  that let one default style every type.
- **Earlier, still true:** limits are data with one source (2026-09-27); work goes in vertical slices (2026-09-27).

## Progress

- 2026-09-27 – 2026-09-28: In-app editor built, reviewed and redesigned (history up to `ac82973`).
- 2026-09-29: Editor replaced by the viewer, file watching, the `catalog` commands and the agent skill.
- 2026-09-29: Review of the viewer in the running app: sharp captions, a path that uses the room it has, a "Live"
  status that shows when the file last changed, a balanced overview grid and examples that clear their captions.

## Next

- Try the skill on real requests and tighten it where an agent goes wrong.
- If agents often need detection attributes beyond the primary classification, extend bindings (see
  [Draw System](draw-system.md#catalog-language)).
