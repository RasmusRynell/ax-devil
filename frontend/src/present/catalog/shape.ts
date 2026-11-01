import type { ExprType } from "../expr/types";
import type { FieldName, MarkName } from "./types";

/** The keys of a closed object in a catalog document, and the ones it must have. */
export interface Shape {
  readonly allowed: readonly string[];
  readonly required: readonly string[];
}

/** The document root. */
export const ROOT: Shape = {
  allowed: ["schema", "id", "name", "description", "origin", "vars", "themes", "rules", "layers"],
  required: ["schema", "id", "name", "description", "layers"],
};

/** A rule: a condition and the variables it sets. */
export const RULE: Shape = { allowed: ["when", "set"], required: ["when", "set"] };

/** A layer. */
export const LAYER: Shape = {
  allowed: ["feature", "for", "when", "marks"],
  required: ["marks"],
};

/** One placement candidate of a label or text. */
export const PLACE: Shape = {
  allowed: ["when", "at", "anchor", "dx", "dy"],
  required: ["at", "anchor"],
};

/** One run of a label. */
export const RUN: Shape = { allowed: ["text", "bar", "color", "weight"], required: ["color"] };

/** What a layer's marks are drawn for. */
export const LAYER_TARGETS = ["entities", "relations"] as const;

export const FIELD_TYPES: Readonly<Record<FieldName, ExprType>> = {
  when: "bool",
  on: "box",
  w: "length",
  h: "length",
  dx: "length",
  dy: "length",
  outset: "length",
  min_size: "length",
  stroke: "color",
  fill: "color",
  width: "length",
  dash: "bool",
  radius: "length",
  r: "length",
  from: "point",
  to: "point",
  gain: "number",
  max: "length",
  head: "length",
  head_width: "length",
  min_speed: "number",
  vx: "number",
  vy: "number",
  value: "number",
  text: "text",
  color: "color",
  size: "length",
  background: "color",
  gap: "length",
};

/** Per mark: the expression fields it takes, the static keys it takes, and the keys it must have. */
export const MARKS: Readonly<
  Record<
    MarkName,
    { fields: readonly FieldName[]; statics: readonly string[]; required: readonly string[] }
  >
> = {
  box: {
    fields: [
      "when",
      "on",
      "w",
      "h",
      "dx",
      "dy",
      "outset",
      "min_size",
      "stroke",
      "fill",
      "width",
      "dash",
      "radius",
    ],
    statics: ["pin", "margin"],
    required: [],
  },
  circle: {
    fields: ["when", "on", "dx", "dy", "min_size", "stroke", "fill", "width", "dash", "r"],
    statics: ["pin", "margin"],
    required: ["r"],
  },
  line: {
    fields: ["when", "min_size", "from", "to", "stroke", "width", "dash"],
    statics: [],
    required: ["from", "to"],
  },
  arrow: {
    fields: [
      "when",
      "on",
      "min_size",
      "gain",
      "max",
      "head",
      "head_width",
      "min_speed",
      "vx",
      "vy",
      "stroke",
      "width",
    ],
    statics: [],
    required: [],
  },
  polygon: {
    fields: ["when", "min_size", "stroke", "fill", "width", "dash"],
    statics: [],
    required: [],
  },
  bar: {
    fields: ["when", "on", "w", "h", "dx", "dy", "min_size", "value", "fill"],
    statics: ["pin", "margin"],
    required: ["value"],
  },
  text: {
    fields: ["when", "on", "min_size", "text", "color", "size"],
    statics: ["font", "place"],
    required: ["text", "color", "size", "place"],
  },
  label: {
    fields: ["when", "on", "min_size", "size", "background", "radius", "gap"],
    statics: ["font", "place", "runs", "pad"],
    required: ["runs", "size", "place"],
  },
};
