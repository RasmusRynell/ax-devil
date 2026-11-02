import type { FieldGroupWire, FieldWire } from "./protocol";

/** The groups in display order, with their headings. */
const GROUPS: readonly (readonly [FieldGroupWire, string])[] = [
  ["classification", "Classification"],
  ["motion", "Motion"],
  ["geometry", "Geometry"],
  ["debug", "Debug"],
  ["raw", "Raw"],
];

/** A field by where it is, not whose it is: its group and its path inside the group. */
export type FieldId = { readonly group: FieldGroupWire; readonly path: readonly string[] };

/** What the inspector keeps across the objects a viewer shows: the pinned fields in pin order, and the folded groups. */
export interface InspectorMemory {
  pins: readonly FieldId[];
  folded: ReadonlySet<FieldGroupWire>;
}

/** A viewer's inspector before the user touched it: nothing pinned, the bulky groups folded. */
export const FRESH_MEMORY: InspectorMemory = {
  pins: [],
  folded: new Set<FieldGroupWire>(["debug", "raw"]),
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
    }
  | { kind: "field"; key: string; id: FieldId; label: string; value: FieldWire["value"] }
  | { kind: "unavailable"; key: string; id: FieldId; label: string };

/** The identity of a field: a path may contain dots, so its parts are not joined by one. */
export const fieldKey = ({ group, path }: FieldId): string => [group, ...path].join("\u0000");

/** `group.path`, as the Objects query names the field. */
export const fieldLabel = ({ group, path }: FieldId): string => [group, ...path].join(".");

/** Pins `id` after the others, or unpins it. */
export function togglePin(pins: readonly FieldId[], id: FieldId): readonly FieldId[] {
  const key = fieldKey(id);
  return pins.some((pin) => fieldKey(pin) === key)
    ? pins.filter((pin) => fieldKey(pin) !== key)
    : [...pins, id];
}

const SOURCE_CAPTION = "Source";
const DECODER_CAPTION = "Computed";

/** The Debug fields, with a caption before those the source reported and those computed, when both exist. */
function debugLines(own: readonly FieldWire[]): Line[] {
  const source = own.filter((field) => field.origin === "source");
  if (source.length === 0) return own.map(fieldLine);
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
    ...source.map(fieldLine),
    ...(rest.length > 0 ? [caption(DECODER_CAPTION), ...rest.map(fieldLine)] : []),
  ];
}

function fieldLine(field: FieldWire): Line {
  const id = { group: field.group, path: field.path };
  return {
    kind: "field",
    key: fieldKey(id),
    id,
    label: field.path.join(".") || "value",
    value: field.value,
  };
}

/**
 * The inspector's rows. `pinned` holds one row per pin, in pin order, each taking the first field with that identity
 * and named by its full `group.path`; a field the object lacks is `unavailable`. `lines` is every group with fields as
 * a caption followed, unless folded, by its fields.
 */
export function inspectorLines(
  fields: readonly FieldWire[],
  pins: readonly FieldId[],
  folded: ReadonlySet<FieldGroupWire>,
): { pinned: Line[]; lines: Line[] } {
  const first = new Map<string, FieldWire>();
  for (const field of fields) {
    const key = fieldKey(field);
    if (!first.has(key)) first.set(key, field);
  }
  const pinned = pins.map((id): Line => {
    const key = fieldKey(id);
    const label = fieldLabel(id);
    const found = first.get(key);
    return found
      ? { kind: "field", key, id, label, value: found.value }
      : { kind: "unavailable", key, id, label };
  });
  const lines = GROUPS.flatMap(([group, heading]): Line[] => {
    const own = fields.filter((field) => field.group === group);
    if (own.length === 0) return [];
    const isFolded = folded.has(group);
    const caption: Line = {
      kind: "caption",
      heading,
      fold: group,
      folded: isFolded,
      copyable: group === "raw",
    };
    if (isFolded) return [caption];
    return [caption, ...(group === "debug" ? debugLines(own) : own.map(fieldLine))];
  });
  return { pinned, lines };
}

/** What the row shows: whole numbers as they are, others to four significant digits (one decimal from 10 000). */
export function displayValue(value: FieldWire["value"]): string {
  if (typeof value !== "number" || Number.isInteger(value)) return String(value);
  return Math.abs(value) >= 1e4 ? value.toFixed(1) : String(Number(value.toPrecision(4)));
}

/** What copying a value puts on the clipboard: all of it. */
export const copyValue = (value: FieldWire["value"]): string => String(value);

/** The Raw fields as a JSON object keyed by dotted path, in field order. */
export function rawJson(fields: readonly FieldWire[]): string {
  const entries = new Map<string, FieldWire["value"]>();
  for (const field of fields)
    if (field.group === "raw") entries.set(field.path.join("."), field.value);
  if (entries.size === 0) return "{}";
  const body = [...entries].map(
    ([key, value]) => `  ${JSON.stringify(key)}: ${JSON.stringify(value)}`,
  );
  return `{\n${body.join(",\n")}\n}`;
}
