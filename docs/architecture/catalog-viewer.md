# Catalog Viewer

Render catalogs (see [Draw System](draw-system.md)) decide how every overlay is drawn. There is no in-app editor
([invariant](../domain/invariants.md#rendering)):
an agent edits the catalog JSON following `.agents/skills/render-catalog/SKILL.md`, and the **catalog viewer** shows
the result the moment the file is saved. Open it with `ax-devil catalog` or **View → Render Catalogs** (`Ctrl+R`).

## Pieces

Code lives in `src/ax_devil/modules/catalog_viewer/`. File watching belongs to `SceneRenderCatalogManager`; see
[Catalog files and selection](../../src/ax_devil/modules/scene/rendering/README.md#catalog-files-and-selection).

| Piece | What it does |
|---|---|
| `sheets.py` | Example sheets: **Overview** (every object type and relation), **Street** (a camera view with overlaps, occlusion and the frame edge), one sheet per object type (sizes, confidences, long ids, movement, edges, over bright, busy and dark ground), one per relation, and **Crowd** (48 objects at night). Ids and attribute values look like real decoder output and are seeded, so sheets are stable. |
| `street.py`, `footage.py` | The street scene and the painted backgrounds the sheets draw over. |
| `window.py` | `CatalogViewerWindow`: catalog choice, **New copy**, **Delete**, **Apply to all**, **Use as default**, live/error status and a tab per sheet. One viewer per manager; it redraws only when visible and something changed. |
| `cli.py` | `ax-devil catalog` opens the viewer; `list`, `check [FILE]`, `render [FILE] --out DIR [--sheet KEY]`, `new NAME [--from FILE]`, `use FILE` and `reference`. `render` writes exactly the sheets the viewer shows, so an agent can look at its work. |
| `reference.py` | Generates the language reference `ax-devil catalog reference` prints from the runtime definitions, so it cannot drift. |

The loader reads each catalog revision once and returns its source document with the compiled catalog; sheets always
draw the document that was compiled. A failed reload keeps that file's last good preview; switching to a file that
does not load clears the previous file's preview and description.

## Design rules

- **Agents edit, the app shows.** There is no in-app editor; changing the look takes a sentence to an agent.
- **Sheets look like real footage and data.** Bright and busy backgrounds, UUID ids and uneven attribute values make
  weak designs visible. Situation sheets are captioned; captions are drawn as overlay text at a fixed screen size, so
  they stay sharp at any zoom and in `render` output.
- **Built-in catalogs are read-only** ([invariant](../domain/invariants.md#rendering)), so the viewer lists them
  first without **Delete** and users copy one with **New copy**. **Delete** removes a user catalog file, including
  one that does not load; a catalog that does not load cannot be copied.
- **The viewer follows the chosen default.** After `ax-devil catalog use`, the viewer switches to the new default.
  When that file stops loading, open views keep their last good catalog and new views fall back to the built-in one,
  while the viewer and `catalog check` keep showing the broken file and its error.
- **The main window closes the viewer** (`close_catalog_viewer()` in `MainWindow.closeEvent`), so the viewer releases
  its renderer before its owner is destroyed.

## Not yet verified

File watching on Windows, macOS and network file systems (the catalog selector's **Reload** button in each video
view is the fallback);
**New copy** and **Delete** clicked by hand in the running app; an agent session driven only by the skill.
