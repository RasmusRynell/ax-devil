---
name: render-catalog
description: Change how ax-devil draws overlays on video — boxes, labels, ids, class names, font sizes, colors, line styles, confidence bars, speed arrows, badges, swatches, relation lines — by editing a render catalog JSON file. Use whenever the user asks to change, add, hide or restyle anything drawn over the video, for all objects or one type (people, vehicles, heads...), or asks why an overlay looks the way it does.
---

# Changing how overlays look: render catalogs

Everything ax-devil draws over video comes from a **render catalog**, a JSON file. You change the look by editing
that file. The user does not read the JSON: they describe what they want in their own words, and they watch the
**catalog viewer** (`uv run ax-devil catalog`, or View → Render Catalogs in the app), which redraws the catalog on
example objects every time the file is saved; suggest it if they are not watching yet. Your job is to turn their words into a clean, minimal change to the file, check it, look at the result, and tell
them in plain words what changed.

## The loop

1. **Find the file** the user is looking at (below).
2. **Render it before you change it**, so you know what it looks like now:
   `uv run ax-devil catalog render --out /tmp/catalog-before`
3. **Read the parts that matter** and decide *where* the change belongs (see "Writing it well").
4. **Edit the JSON** with a minimal diff. The viewer and every open video view pick it up on save.
5. **Check it:** `uv run ax-devil catalog check` loads the file and draws every example sheet. Fix anything it reports;
   errors name their place in the file, such as `$.templates.id_tab.parameters.font_size.default`.
6. **Look at it:** `uv run ax-devil catalog render --out /tmp/catalog-after` and open the PNGs (use `--sheet` for one:
   `overview`, `street`, `crowd`, or an object type or relation id). Compare with the before images. Does it do what was
   asked, and nothing else? Is anything now unreadable, clipped or clashing?
7. **Report** what changed as the user sees it ("ids are now 11 px on every object type; people keep their own green
   boxes"), not the JSON. Mention anything you could not do and why.

Commands take the default catalog when no file is given; pass a path to work on another one.

## Which file

`uv run ax-devil catalog list` lists every catalog and its file; `*` marks the default, which new views use. Users
create and delete catalogs themselves in the viewer (**New copy**, **Delete**), which also shows the file of the
catalog on screen.

- **The user names a catalog** ("my night shift catalog"), or gives its file: edit that one. If the name matches
  several, or none, ask.
- **Otherwise, the default is a user catalog:** edit that file.
- **The default is a built-in catalog** (Standard, Minimal, Chunky, Glass, Tracking or Classic, marked
  `(built-in, read-only)`): it ships with the app. For the user's own preferences, copy it, make the copy the default
  and edit the copy. The viewer switches to it on its own:
  ```
  uv run ax-devil catalog new "My overlays" --from <built-in file>   # prints the new file's path
  uv run ax-devil catalog use <that path>
  ```
- Edit a built-in file in `src/ax_devil/modules/scene/rendering/catalog_definitions/` only when the user asks to
  change what the app ships. Then also run `make test`, which checks the built-in catalogs.

## How a catalog is built

Read `uv run ax-devil catalog reference` for every type, operation, primitive and input with its exact name, type and
bounds. Read the built-in catalog the user's catalog came from as the model to imitate: its structure, naming and
level of detail are the house style.

```jsonc
{
  "metadata": {"id": "user.my_overlays", "name": "My overlays", "schema_version": 3, "description": "..."},
  "templates": {            // components: reusable drawings with typed parameters, e.g. "id_tab"
    "<component id>": {"label": "...", "description": "...", "parameters": {...}, "values": {...}, "steps": [...]}
  },
  "recipes": {
    "classifications": [...], // one object type per entry, chosen by the detection's class
    "fallbacks": [...],       // exactly two: selector kind "unclassified" and "classified" (classes with no type)
    "relations": [...]        // drawn between two objects, chosen by relation type
  }
}
```

**Routing.** An object uses the classification recipe whose `selector.types` contains its class (`"human"`, `"car"`,
...); otherwise the `classified` fallback; with no class at all, the `unclassified` fallback. A class may belong to
only one recipe. Known classes are in `KnownClassificationType` in `src/ax_devil/modules/scene/model.py`.

**Recipes** (object types and relations) have `id`, `label`, `description`, `selector`, optionally `enabled`,
`bindings` and `values`, and `steps`. **Drawing order is by layer, not by step**: shapes, then text, then labels.
Within a layer the renderer may draw in any order, so a shape never reliably covers another shape. Put a fill and its
outline in one primitive, keep shapes that must stay visible from overlapping, and put anything that sits on a
background in a label. A step is either

- a component: `{"label": "Label", "template": "id_tab", "inputs": {"geometry": ..., "object_id": ...}}`, or
- a primitive: `{"label": "Outline", "primitive": "box", "fields": {"geometry": ...}, "style": {...}}`,

and may have `"feature": "confidence"` (the view's Details control gates this component),
`"enabled": <bool expression>` (draw only when true) and `"visible": false` (kept but not drawn).

**User visibility.** Schema v3 templates and steps may declare `feature`: `outlines`, `ids`, `class_names`,
`confidence`, `speed`, `movement`, `attributes`, or `relations`. Tag shared semantic templates once; tag individual
steps for generic helpers. Both gates apply on an invocation, and untagged components remain permitted. Give all
parts of one presentation the same feature, including backgrounds and arrowheads. Split mixed components into
separate tagged steps when independent visibility is needed. Details preferences belong to the view, not the JSON;
`visible: false` remains an unconditional catalog choice.
All-enabled catalog previews show the authored appearance regardless of an individual lane's choices.

**Components** (`templates`) declare `parameters`. A parameter has a `type`, a `label`, usually a `default` (or
`"required": true`), and may have `description`, `"nullable": true` and bounds (`minimum`, `exclusive_minimum`,
`maximum` for numbers and lengths, `max_length` for text). A component sees only its parameters, its own `values` and
`context`. **A parameter's default is the value every type gets unless a recipe step passes its own input**: this
is how one change styles all objects at once.

**Expressions.** Anywhere a value goes you can write:

- a literal: `8`, `"dash"`, `[255, 255, 255]`, `{"value": 8, "unit": "px"}` (strings are always literal text);
- a reference: `{"ref": ["scene", "observation", "geometry"]}`, `{"ref": ["parameters", "color"]}`,
  `{"ref": ["values", "bbox_color"]}`, `{"ref": ["bindings", "carries_bag"]}`;
- a call: `{"call": "box_anchor", "args": {"geometry": ..., "anchor": "top-left"}}`;
- `{"literal": ...}` for data that would otherwise look like a ref or call.

`values` are named calculations, computed when used; order does not matter.

**Units and styles.** Sizes are lengths: `{"value": 2, "unit": "px"}` for fixed screen pixels, or `image_width`,
`image_height`, `image_min` for a fraction of the picture. Colors are `[r, g, b]` with integers 0–255. A shape style
is `{"stroke": {"color", "width", "pattern": "solid"|"dash"}, "fill": {"color", "alpha": 0–255}, "radius"?}`; stroke
and fill may be `null`, and `radius` rounds a box's corners. Dashes and the gaps between them are each about three
line widths long. A text style is `{"text": {"color", "size", "family"?}}`.

**Labels** are the way to put text on a background, such as a tag on a box: the renderer sizes the background to the
text, so there is nothing to measure. Fields are `position`, `anchor` (which point of the background sits on
`position`) and `runs`, a list of `{"text", "color", "weight": "regular"|"medium"|"semibold"|"bold"}` laid out left to
right; a run whose text is null or empty is left out. A run with `"bar": <number 0–1>` instead of `text` is a small
meter, such as a confidence bar, filled that much in its color over a faint track of the same color. The style is
`{"label": {"size", "family", "background": {"color", "alpha"} | null, "padding_x", "padding_y", "radius", "gap"}}`.
A colored dot is a run with the text `●`.

**Long ids**: `trim_text` with `"elide": "middle"` keeps both ends of text longer than `max_length`, so
`3f2a9c1e-7b4d-…-1a2b3c4d5e6f` shows as `3f2a9…d5e6f` and `track-000412` as `track…00412`.

**Detection data** that is not in `scene` comes through `bindings` on an object type:
`{"source": "primary_classification_attribute", "attribute": "carries_bag", "value_shape": "bool", "picker": "value"}`.
A binding is missing (null) when the detection lacks the attribute, so guard steps that use it with
`"enabled": {"call": "is_present", "args": {"value": {"ref": ["bindings", "<name>"]}}}`. Attribute names come from the
decoders: `git grep -n 'Attribute(name=' src/ax_devil/plugins/decoders`.

## Writing it well

The user never sees the JSON, so its quality is on you. Aim for the change a careful maintainer would make.

- **Change it where it belongs.**
  - "Everywhere" or "all objects": change the component parameter's `default` (for example
    `templates.id_tab.parameters.font_size.default`). Do not paste the same input into every recipe.
  - One type only: set that input on that recipe's step. Before a global change, check which recipes already pass
    their own input for it; those do not follow the default, so decide with the user's intent whether to remove them.
  - Something that should vary but is hard-coded inside a component: make it a parameter whose default is today's
    value, so nothing else changes, then set it. Give it a `label`, a `description` when useful, and bounds that
    reject nonsense (`exclusive_minimum: 0` for sizes).
- **Stay minimal.** Do not write inputs equal to the default; remove overrides that end up equal to it. Delete steps
  the user wants gone rather than leaving hidden clutter; use `"visible": false` only for "hide it for now". Don't
  reformat or reorder untouched parts: keep the file's two-space indentation and key order, so the diff shows only
  the change.
- **Reuse before you add.** Use an existing component when one draws what is needed. Put a new drawing that more
  than one type uses in a new component; a one-off can be a primitive step on its recipe.
- **Name things for people.** Every new component, parameter, step and recipe gets a short `label` in plain words
  (the viewer and error messages show them) and a `description` when the purpose is not obvious. Ids are
  `snake_case` and stable; do not rename existing ids.
- **Follow the house style** of the built-in catalogs: the same component split (box, id and score labels, speed
  arrow, attributes), `px` for fixed on-screen sizes, anchors instead of hand-computed offsets, and
  `color_ramp` for colors that follow a number, `pick_color` for a color that follows an id.
- **Mind the whole picture.** Check the Overview, Street and Crowd sheets after any global change: readable text on
  both small and large objects, no new overlaps, and color that still separates the object types.
- **Ask only when the request is genuinely ambiguous** in a way that changes the result (all types or just one?),
  and say what you assumed otherwise.

## What the language cannot do

There are no images, animation, automatic text wrapping or text joining (use separate label runs instead), and
drawings can only read the inputs
`ax-devil catalog reference` lists. If a request needs a new operation, primitive or detection input, that is a code
change in `src/ax_devil/modules/scene/rendering/` (see `docs/architecture/draw-system.md`); tell the user rather than
approximating it badly.
