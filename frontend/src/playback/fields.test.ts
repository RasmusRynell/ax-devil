import { describe, expect, it } from "vite-plus/test";
import {
  copyValue,
  displayValue,
  FRESH_MEMORY,
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
      "7",
    );
    expect(
      first.pinned.map((line) =>
        line.kind === "field" || line.kind === "unavailable" ? line.label : "",
      ),
    ).toEqual(["raw.a.b", "classification.class", "raw.a.b"]);
    // A key containing a dot is not the nested name, and the first match supplies the value.
    expect(shown(first.pinned)).toEqual([2, "car", 1]);

    const next = inspectorLines([field(CLASS, "bus"), field(CLASS, "ignored")], pins, NONE, "7");
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
    expect(copyValue({ value: 0.123456 })).toBe("0.123456");
    expect(copyValue({ value: null })).toBe("null");
    expect(copyValue({ value: 0.9, rgb: [255, 10, 0] })).toBe("0.9 #ff0a00");
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

describe("the list", () => {
  const colour = (parent: string, name: string, rgb: [number, number, number]): FieldWire => ({
    group: "classification",
    path: ["attributes", parent, name],
    value: 0.5,
    rgb,
  });
  const fields = [
    field(CLASS, "car"),
    colour("vehicle_colors", "red", [255, 0, 0]),
    colour("vehicle_colors", "blue", [0, 0, 255]),
    colour("upper_clothing_colors", "red", [255, 0, 0]),
    { group: "images" as const, path: ["0", "data"], value: "3 bytes" },
  ];

  it("leads with the id, which cannot be pinned, and puts a heading before each run of colours, uncounted", () => {
    const { lines } = inspectorLines(fields, [], NONE, "7");
    expect(lines.map((line) => (line.kind === "caption" ? line.heading : line.kind))).toEqual([
      "field",
      "Classification",
      "field",
      "heading",
      "field",
      "field",
      "heading",
      "field",
      "Images",
      "field",
    ]);
    expect(lines[0]).toMatchObject({ label: "id", value: "7", id: null });
    expect(lines.filter((line) => line.kind === "heading")).toEqual([
      { kind: "heading", heading: "vehicle colors" },
      { kind: "heading", heading: "upper clothing colors" },
    ]);
    expect(lines.filter((line) => line.kind === "caption").map((line) => line.count)).toEqual([
      4, 1,
    ]);
    // A colour is named by its colour.
    expect(lines.find((line) => line.kind === "field" && line.rgb)).toMatchObject({ label: "red" });
  });

  it("starts with Debug, Raw and Images folded", () => {
    const { lines } = inspectorLines(fields, [], FRESH_MEMORY.folded, "7");
    expect(lines.filter((line) => line.kind === "caption").map((line) => line.folded)).toEqual([
      false,
      true,
    ]);
  });
});
