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
import type { FieldGroupWire, FieldWire, LeafWire } from "../stream/protocol";

const DOTTED: FieldId = { group: "raw", path: ["a.b"] };
const NESTED: FieldId = { group: "raw", path: ["a", "b"] };
const CLASS: FieldId = { group: "classification", path: ["class"] };
const text = (value: string): LeafWire => ({ kind: "text", value });
const int = (value: string): LeafWire => ({ kind: "int", value });
const float = (value: number): LeafWire => ({ kind: "float", value });
const NULL: LeafWire = { kind: "null" };
const field = ({ group, path }: FieldId, value: LeafWire): FieldWire => ({
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
      [field(CLASS, text("car")), field(NESTED, int("1")), field(DOTTED, int("2"))],
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
    expect(shown(first.pinned)).toEqual([int("2"), text("car"), int("1")]);

    const next = inspectorLines(
      [field(CLASS, text("bus")), field(CLASS, text("ignored"))],
      pins,
      NONE,
      "7",
    );
    expect(shown(next.pinned)).toEqual(["unavailable", text("bus"), "unavailable"]);
    expect(togglePin(pins, CLASS).map(fieldKey)).toEqual([fieldKey(DOTTED), fieldKey(NESTED)]);
  });
});

describe("values", () => {
  it("are shown rounded and copied whole", () => {
    const leaves: LeafWire[] = [
      int("3"),
      float(0.123456),
      float(-12.3456),
      float(12345.678),
      float(1e21),
      { kind: "bool", value: true },
      NULL,
      text("text"),
    ];
    expect(leaves.map(displayValue)).toEqual([
      "3",
      "0.1235",
      "-12.35",
      "12345.7",
      "1e+21",
      "true",
      "null",
      "text",
    ]);
    expect(copyValue({ value: float(0.123456) })).toBe("0.123456");
    expect(copyValue({ value: NULL })).toBe("null");
    expect(copyValue({ value: float(0.9), rgb: [255, 10, 0] })).toBe("0.9 #ff0a00");
  });

  it("keep an integer beyond 2^53 exact when shown, copied and written as JSON", () => {
    const value = int("9223372036854775807");
    expect(displayValue(value)).toBe("9223372036854775807");
    expect(copyValue({ value })).toBe("9223372036854775807");
    expect(rawJson([field(NESTED, value)])).toBe('{\n  "a.b": 9223372036854775807\n}');
  });

  it("copy a Raw group as JSON keyed by dotted path", () => {
    const fields = [
      field(CLASS, text("car")),
      field(NESTED, int("1")),
      { group: "raw" as const, path: ["n"], value: NULL },
    ];
    expect(rawJson(fields)).toBe('{\n  "a.b": 1,\n  "n": null\n}');
    expect(rawJson([])).toBe("{}");
    // Paths that join to the same key all stay.
    expect(
      rawJson([field(DOTTED, int("1")), field(NESTED, int("2")), field(NESTED, int("3"))]),
    ).toBe('{\n  "a.b": 1,\n  "a.b (2)": 2,\n  "a.b (3)": 3\n}');
  });
});

describe("the list", () => {
  const colour = (parent: string, name: string, rgb: [number, number, number]): FieldWire => ({
    group: "classification",
    path: ["attributes", parent, name],
    value: float(0.5),
    rgb,
  });
  const fields = [
    field(CLASS, text("car")),
    colour("vehicle_colors", "red", [255, 0, 0]),
    colour("vehicle_colors", "blue", [0, 0, 255]),
    colour("upper_clothing_colors", "red", [255, 0, 0]),
    { group: "images" as const, path: ["0", "data"], value: text("3 bytes") },
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
    expect(lines[0]).toMatchObject({ label: "id", value: text("7"), id: null });
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
