import { MAX_INSPECTION_PINS } from "../generated/api";
import type { FieldGroupWire, FieldWire, LeafWire } from "../stream/protocol";

/** The groups in display order, with their headings. */
const GROUPS: readonly (readonly [FieldGroupWire, string])[] = [
  ["classification", "Classification"],
  ["motion", "Motion"],
  ["geometry", "Geometry"],
  ["debug", "Debug"],
  ["raw", "Raw"],
  ["images", "Images"],
];

/** A field by where it is, not whose it is: its group and its path inside the group. */
export type FieldId = {
  readonly identity?: string;
  readonly group: FieldGroupWire;
  readonly path: readonly string[];
};

/** What the inspector keeps across the objects a viewer shows: the pinned fields in pin order, and the folded groups. */
export interface InspectorMemory {
  pins: readonly FieldId[];
  folded: ReadonlySet<FieldGroupWire>;
}

/** A viewer's inspector before the user touched it: nothing pinned, the bulky groups folded. */
export const FRESH_MEMORY: InspectorMemory = {
  pins: [],
  folded: new Set<FieldGroupWire>(["debug", "raw", "images"]),
};

/** One row of the inspector. */
export type Line =
  | {
      kind: "caption";
      heading: string;
      /** The group this caption folds; none for a caption inside a group. */
      fold: FieldGroupWire | null;
      folded: boolean;
      /** Whether the caption copies its group, as JSON. */
      copyable: boolean;
      /** How many fields the group holds, not counting headings; none for a caption inside a group. */
      count?: number;
    }
  /** Names the run of colour fields that follows; it is not a field, so it cannot be copied, pinned or counted. */
  | { kind: "heading"; heading: string }
  | {
      kind: "field";
      key: string;
      /** Where the field is, which pins follow; null for the object's id, which cannot be pinned. */
      id: FieldId | null;
      label: string;
      value: FieldWire["value"];
      cut?: FieldWire["cut"];
      pathCut?: FieldWire["path_cut"];
      /** The colour a colour field's value is the score of. */
      rgb?: FieldWire["rgb"];
      /** What the row shows in place of the value. */
      text?: FieldWire["text"];
      /** How much of a bar the value fills, 0 to 1; no bar when absent. */
      fraction?: FieldWire["fraction"];
      /** Where a value too long for its row loses its middle, as an id does; it loses its end otherwise. */
      elide?: "middle";
      /** What reported the field, which tells apart fields of one path, such as a pinned one and its namesake. */
      origin?: FieldWire["origin"];
    }
  | { kind: "unavailable"; key: string; id: FieldId; label: string; pending?: boolean };

/** The object's id, the row that leads the list. */
const idLine = (entity: string): Line => ({
  kind: "field",
  key: "id",
  id: null,
  label: "id",
  value: { kind: "text", value: entity },
  elide: "middle",
});

/** The identity of a field: a path may contain dots, so its parts are not joined by one. */
export const fieldKey = ({ group, path, identity }: FieldId): string =>
  identity ? `${group}\u0000sha256:${identity}` : [group, ...path].join("\u0000");

/** `group.path`, as the Objects query names the field. */
export const fieldLabel = ({ group, path }: FieldId): string => [group, ...path].join(".");

/** Saved path-only pins and new digest pins identify the same field; two digests always compare in full. */
export const samePin = (left: FieldId, right: FieldId): boolean =>
  left.group === right.group &&
  (left.identity && right.identity
    ? left.identity === right.identity
    : left.path.length === right.path.length &&
      left.path.every((part, i) => part === right.path[i]));

/** Pins `id` after the others, or unpins it. */
export function togglePin(pins: readonly FieldId[], id: FieldId): readonly FieldId[] {
  return pins.some((pin) => samePin(pin, id))
    ? pins.filter((pin) => !samePin(pin, id))
    : pins.length < MAX_INSPECTION_PINS
      ? [...pins, id]
      : pins;
}

const SOURCE_CAPTION = "Source";
const DECODER_CAPTION = "Computed";

/** The fields as lines, with a heading before each run of colour fields that share a parent path. */
function fieldLines(own: readonly FieldWire[]): Line[] {
  const lines: Line[] = [];
  let run: string | null = null;
  for (const field of own) {
    const owner = field.rgb && field.path.length > 1 ? field.path.slice(0, -1) : null;
    const key = owner?.join("\u0000") ?? null;
    if (owner && key !== run)
      lines.push({ kind: "heading", heading: owner[owner.length - 1].replaceAll("_", " ") });
    run = key;
    lines.push(fieldLine(field));
  }
  return lines;
}

/** The Debug fields, with a caption before those the source reported and those computed, when both exist. */
function debugLines(own: readonly FieldWire[]): Line[] {
  const source = own.filter((field) => field.origin === "source");
  if (source.length === 0) return fieldLines(own);
  const rest = own.filter((field) => field.origin !== "source");
  const caption = (heading: string): Line => ({
    kind: "caption",
    heading,
    fold: null,
    folded: false,
    copyable: false,
  });
  return [
    caption(SOURCE_CAPTION),
    ...fieldLines(source),
    ...(rest.length > 0 ? [caption(DECODER_CAPTION), ...fieldLines(rest)] : []),
  ];
}

/** A colour field is named by its colour, the last part of its path. */
function fieldLine(field: FieldWire): Line {
  // New saved pins retain only a display preview; their digest resolves the full path on every sample.
  let left = 64 * 1024;
  const path = field.identity
    ? field.path.map((part) => {
        let shown = part;
        if (part.length > left) {
          let end = left;
          if (end > 0 && /[\uD800-\uDBFF]/.test(part[end - 1])) end--;
          shown = new TextDecoder().decode(new TextEncoder().encode(part.slice(0, end)));
        }
        left -= shown.length;
        return shown;
      })
    : field.path;
  const id = { group: field.group, path, identity: field.identity };
  return {
    kind: "field",
    key: fieldKey(id),
    id,
    label: (field.rgb ? field.path.slice(-1) : field.path).join(".") || "value",
    value: field.value,
    cut: field.cut,
    pathCut: field.path_cut,
    rgb: field.rgb,
    text: field.text,
    fraction: field.fraction,
    origin: field.origin,
  };
}

/**
 * The inspector's rows. `pinned` holds one row per pin, in pin order, each taking the first field with that identity
 * and named by its full `group.path`; a field the object lacks is `unavailable`. `lines` is the object's `entity` id
 * and then every group with fields as a caption followed, unless folded, by its fields.
 */
export function inspectorLines(
  fields: readonly FieldWire[],
  pins: readonly FieldId[],
  folded: ReadonlySet<FieldGroupWire>,
  entity: string,
  pinFields?: readonly (FieldWire | null | undefined)[],
): { pinned: Line[]; lines: Line[] } {
  const first = new Map<string, FieldWire>();
  for (const field of fields) {
    const key = fieldKey(field);
    if (!first.has(key)) first.set(key, field);
  }
  const pinned = pins.map((id, index): Line => {
    const key = fieldKey(id);
    const label = fieldLabel(id);
    const found = pinFields ? pinFields[index] : first.get(key);
    return found
      ? {
          kind: "field",
          key,
          id,
          label,
          value: found.value,
          cut: found.cut,
          pathCut: found.path_cut,
          rgb: found.rgb,
          text: found.text,
          fraction: found.fraction,
          origin: found.origin,
        }
      : {
          kind: "unavailable",
          key,
          id,
          label,
          pending: pinFields !== undefined && found === undefined,
        };
  });
  const groups = GROUPS.flatMap(([group, heading]): Line[] => {
    const own = fields.filter((field) => field.group === group);
    if (own.length === 0) return [];
    const isFolded = folded.has(group);
    const caption: Line = {
      kind: "caption",
      heading,
      fold: group,
      folded: isFolded,
      copyable: group === "raw",
      count: own.length,
    };
    if (isFolded) return [caption];
    return [caption, ...(group === "debug" ? debugLines(own) : fieldLines(own))];
  });
  return { pinned, lines: [idLine(entity), ...groups] };
}

/** All of a leaf as text: an integer's digits as they are, a float as JavaScript prints it, null as "null". */
export function leafText(leaf: LeafWire): string {
  return leaf.kind === "null" ? "null" : String(leaf.value);
}

/** What the row shows: integers and other leaves in full, floats to four significant digits (one decimal from 10 000). */
export function displayValue(leaf: LeafWire): string {
  if (leaf.kind !== "float" || Number.isInteger(leaf.value)) return leafText(leaf);
  const { value } = leaf;
  return Math.abs(value) >= 1e4 ? value.toFixed(1) : String(Number(value.toPrecision(4)));
}

/** What copying a field puts on the clipboard: all of its value, then ` #rrggbb` for a colour field. */
export const copyValue = ({ value, rgb }: Pick<FieldWire, "value" | "rgb">): string =>
  rgb ? `${leafText(value)} ${hexOf(rgb)}` : leafText(value);

/** `#rrggbb`, in lowercase. */
export const hexOf = (rgb: readonly number[]): string =>
  `#${rgb.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`;

/**
 * The Raw fields as a JSON object keyed by dotted path, in field order. Paths that join to the same key (`["a.b"]`
 * and `["a", "b"]`) keep every value: the later ones get " (2)", " (3)" after the key.
 */
export function rawJson(fields: readonly FieldWire[]): string {
  const keys = new Set<string>();
  const body: string[] = [];
  for (const field of fields) {
    if (field.group !== "raw") continue;
    const dotted = field.path.join(".");
    let key = dotted;
    for (let copy = 2; keys.has(key); copy++) key = `${dotted} (${copy})`;
    keys.add(key);
    // An integer is written as its digits, not through a number, so it stays exact beyond 2^53.
    const { value } = field;
    const json =
      value.kind === "int"
        ? value.value
        : JSON.stringify(value.kind === "null" ? null : value.value);
    body.push(`  ${JSON.stringify(key)}: ${json}`);
  }
  return body.length === 0 ? "{}" : `{\n${body.join(",\n")}\n}`;
}
