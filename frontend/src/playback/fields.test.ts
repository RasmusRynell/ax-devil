import { describe, expect, it } from "vite-plus/test";
import {
  copyValue,
  displayValue,
  fieldKey,
  inspectorLines,
  rawJson,
  togglePin,
  type FieldId,
  type Line,
} from "./fields";
import type { FieldGroupWire, FieldWire } from "./protocol";

const DOTTED: FieldId = { group: "raw", path: ["a.b"] };
const NESTED: FieldId = { group: "raw", path: ["a", "b"] };
const CLASS: FieldId = { group: "classification", path: ["class"] };
const field = ({ group, path }: FieldId, value: FieldWire["value"]): FieldWire => ({
  group,
  path: [...path],
  value,
});
const NONE = new Set<FieldGroupWire>();
const shown = (lines: readonly Line[]) =>
  lines.map((line) => (line.kind === "field" ? line.value : line.kind));

describe("pinned fields", () => {
  it("follow a field's group and path to each object, in pin order, and say when it is missing", () => {
    const pins = [DOTTED, CLASS, NESTED].reduce<readonly FieldId[]>(togglePin, []);
    const first = inspectorLines(
      [field(CLASS, "car"), field(NESTED, 1), field(DOTTED, 2)],
      pins,
      NONE,
    );
    expect(first.pinned.map((line) => (line.kind === "caption" ? "" : line.label))).toEqual([
      "raw.a.b",
      "classification.class",
      "raw.a.b",
    ]);
    // A key containing a dot is not the nested name, and the first match supplies the value.
    expect(shown(first.pinned)).toEqual([2, "car", 1]);

    const next = inspectorLines([field(CLASS, "bus"), field(CLASS, "ignored")], pins, NONE);
    expect(shown(next.pinned)).toEqual(["unavailable", "bus", "unavailable"]);
    expect(togglePin(pins, CLASS).map(fieldKey)).toEqual([fieldKey(DOTTED), fieldKey(NESTED)]);
  });
});

describe("values", () => {
  it("are shown rounded and copied whole", () => {
    expect([3, 0.123456, -12.3456, 12345.678, 1e21, true, null, "text"].map(displayValue)).toEqual([
      "3",
      "0.1235",
      "-12.35",
      "12345.7",
      "1e+21",
      "true",
      "null",
      "text",
    ]);
    expect(copyValue(0.123456)).toBe("0.123456");
    expect(copyValue(null)).toBe("null");
  });

  it("copy a Raw group as JSON keyed by dotted path", () => {
    const fields = [
      field(CLASS, "car"),
      field(NESTED, 1),
      { group: "raw" as const, path: ["n"], value: null },
    ];
    expect(rawJson(fields)).toBe('{\n  "a.b": 1,\n  "n": null\n}');
    expect(rawJson([])).toBe("{}");
    // Paths that join to the same key all stay.
    expect(rawJson([field(DOTTED, 1), field(NESTED, 2), field(NESTED, 3)])).toBe(
      '{\n  "a.b": 1,\n  "a.b (2)": 2,\n  "a.b (3)": 3\n}',
    );
  });
});
