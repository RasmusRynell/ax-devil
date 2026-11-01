import { ANCHORS } from "../expr/checkCall";
import type { Scope } from "../expr/check";
import { type CLength, type Compiled, konst } from "../expr/compiled";
import { didYouMean } from "../expr/diagnostic";
import type { ExprType } from "../expr/types";
import { constantLength } from "../expr/lowerLength";
import { arrayAt, closedObject, pointerTo, stringAt } from "./document";
import type { Session } from "./session";
import {
  type FieldName,
  MARK_TYPES,
  type MarkName,
  type MarkSpec,
  type PlaceSpec,
  type RunSpec,
  WEIGHTS,
  type WeightName,
} from "./types";

const FIELD_TYPES: Readonly<Record<FieldName, ExprType>> = {
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
const MARKS: Readonly<
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

/** A mark being built: expressions are lowered after every variable's type is settled, then land here. */
interface Draft {
  type: MarkName;
  layer: number;
  index: number;
  pointer: string;
  fields: Partial<Record<FieldName, Compiled>>;
  margin: CLength[] | null;
  pad: CLength[] | null;
  pin: readonly [number, number] | null;
  font: string | null;
  place: PlaceSpec[];
  runs: RunSpec[];
}

/** The layer a mark belongs to, as marks need it. */
export interface LayerContext {
  readonly index: number;
  readonly relations: boolean;
  readonly scope: Scope;
}

function anchorAt(
  session: Session,
  value: unknown,
  pointer: string,
): readonly [number, number] | null {
  const name = stringAt(value, pointer, session.diag);
  if (name === null) return null;
  const factors = ANCHORS[name];
  if (!factors) {
    session.diag.add(
      "value",
      { pointer, spans: false },
      null,
      `unknown anchor '${name}'${didYouMean(name, Object.keys(ANCHORS))}`,
    );
    return null;
  }
  return factors;
}

function lengthsAt(
  session: Session,
  value: unknown,
  pointer: string,
  scope: Scope,
  count: number,
  allowOne: boolean,
): CLength[] | null {
  const list = Array.isArray(value)
    ? value
    : allowOne
      ? Array.from({ length: count }, () => value)
      : value;
  if (!Array.isArray(list) || list.length !== count) {
    session.diag.add(
      "value",
      { pointer, spans: false },
      null,
      allowOne ? `expected one length or ${count} lengths` : `expected ${count} lengths`,
    );
    return null;
  }
  const lengths: CLength[] = Array.from({ length: count }, () => constantLength(0));
  const shared = !Array.isArray(value);
  list.forEach((item, i) => {
    // One written length is checked once, however many sides it covers.
    if (shared && i > 0) return;
    session.field(item, shared ? pointer : pointerTo(pointer, i), scope, "length", (compiled) => {
      if (compiled.type !== "length") return;
      if (shared) lengths.fill(compiled.length);
      else lengths[i] = compiled.length;
    });
  });
  return lengths;
}

function placeAt(
  session: Session,
  value: unknown,
  pointer: string,
  scope: Scope,
  into: PlaceSpec[],
): void {
  const list = arrayAt(value, pointer, session.diag);
  if (!list) return;
  if (list.length === 0 || list.length > 8) {
    session.diag.add(
      "value",
      { pointer, spans: false },
      null,
      "a placement list has 1 to 8 candidates",
    );
    return;
  }
  list.forEach((entry, i) => {
    const at = pointerTo(pointer, i);
    const object = closedObject(
      entry,
      at,
      ["when", "at", "anchor", "dx", "dy"],
      ["at", "anchor"],
      session.diag,
    );
    if (!object) return;
    const place = {
      when: null,
      at: [0, 0],
      anchor: [0, 0],
      dx: constantLength(0),
      dy: constantLength(0),
    } as { -readonly [K in keyof PlaceSpec]: PlaceSpec[K] };
    into[i] = place;
    place.at = anchorAt(session, object.at, pointerTo(at, "at")) ?? place.at;
    place.anchor = anchorAt(session, object.anchor, pointerTo(at, "anchor")) ?? place.anchor;
    if ("when" in object) {
      session.field(object.when, pointerTo(at, "when"), scope, "bool", (c) => {
        if (c.type === "bool") place.when = c.scalar;
      });
    }
    for (const key of ["dx", "dy"] as const) {
      if (key in object) {
        session.field(object[key], pointerTo(at, key), scope, "length", (c) => {
          if (c.type === "length") place[key] = c.length;
        });
      }
    }
  });
}

function runsAt(
  session: Session,
  value: unknown,
  pointer: string,
  scope: Scope,
  into: RunSpec[],
): void {
  const list = arrayAt(value, pointer, session.diag);
  if (!list) return;
  if (list.length === 0)
    session.diag.add("value", { pointer, spans: false }, null, "a label needs at least one run");
  list.forEach((entry, i) => {
    const at = pointerTo(pointer, i);
    const object = closedObject(
      entry,
      at,
      ["text", "bar", "color", "weight"],
      ["color"],
      session.diag,
    );
    if (!object) return;
    if ("text" in object === "bar" in object) {
      session.diag.add(
        "value",
        { pointer: at, spans: false },
        null,
        "a run has either 'text' or 'bar'",
      );
      return;
    }
    let weight: WeightName = "regular";
    if ("weight" in object) {
      const name = stringAt(object.weight, pointerTo(at, "weight"), session.diag);
      if (name !== null && !(WEIGHTS as readonly string[]).includes(name)) {
        session.diag.add(
          "value",
          { pointer: pointerTo(at, "weight"), spans: false },
          null,
          `unknown weight '${name}'${didYouMean(name, WEIGHTS)}`,
        );
      } else if (name !== null) weight = name as WeightName;
    }
    const run = { text: null, bar: null, color: konst(Number.NaN), weight } as {
      -readonly [K in keyof RunSpec]: RunSpec[K];
    };
    into[i] = run;
    session.field(object.color, pointerTo(at, "color"), scope, "color", (c) => {
      if (c.type === "color") run.color = c.scalar;
    });
    const kind = "text" in object ? "text" : "bar";
    session.field(
      object[kind],
      pointerTo(at, kind),
      scope,
      kind === "text" ? "text" : "number",
      (c) => {
        if (c.type === "text") run.text = c.emit;
        else if (c.type === "number") run.bar = c.scalar;
      },
    );
  });
}

/** Check one mark object and queue its lowering; null when it is not an object with a valid `mark`. */
export function compileMark(
  session: Session,
  value: unknown,
  pointer: string,
  layer: LayerContext,
  index: number,
): MarkSpec | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    session.diag.add("value", { pointer, spans: false }, null, "expected an object");
    return null;
  }
  const name = (value as Record<string, unknown>).mark;
  if (typeof name !== "string" || !(MARK_TYPES as readonly string[]).includes(name)) {
    const hint = typeof name === "string" ? didYouMean(name, MARK_TYPES) : "";
    session.diag.add(
      name === undefined ? "missing-key" : "value",
      { pointer: name === undefined ? pointer : pointerTo(pointer, "mark"), spans: false },
      null,
      `'mark' is one of ${MARK_TYPES.join(", ")}${hint}`,
    );
    return null;
  }
  const type = name as MarkName;
  if (layer.relations && type === "polygon") {
    session.diag.add(
      "value",
      { pointer: pointerTo(pointer, "mark"), spans: false },
      null,
      "a relation has no polygon",
    );
    return null;
  }
  const kind = MARKS[type];
  const required = [
    ...kind.required,
    ...(layer.relations && kind.fields.includes("on") ? ["on"] : []),
  ];
  const object = closedObject(
    value,
    pointer,
    ["mark", ...kind.fields, ...kind.statics],
    ["mark", ...required],
    session.diag,
  );
  if (!object) return null;
  const draft: Draft = {
    type,
    layer: layer.index,
    index,
    pointer,
    fields: {},
    margin: null,
    pad: null,
    pin: null,
    font: null,
    place: [],
    runs: [],
  };
  for (const field of kind.fields) {
    if (field in object) {
      session.field(
        object[field],
        pointerTo(pointer, field),
        layer.scope,
        FIELD_TYPES[field],
        (compiled) => {
          draft.fields[field] = compiled;
        },
      );
    }
  }
  if ("pin" in object) draft.pin = anchorAt(session, object.pin, pointerTo(pointer, "pin"));
  if ("font" in object)
    draft.font = stringAt(object.font, pointerTo(pointer, "font"), session.diag);
  if ("margin" in object)
    draft.margin = lengthsAt(
      session,
      object.margin,
      pointerTo(pointer, "margin"),
      layer.scope,
      4,
      true,
    );
  if ("pad" in object)
    draft.pad = lengthsAt(session, object.pad, pointerTo(pointer, "pad"), layer.scope, 2, false);
  if ("place" in object)
    placeAt(session, object.place, pointerTo(pointer, "place"), layer.scope, draft.place);
  if ("runs" in object)
    runsAt(session, object.runs, pointerTo(pointer, "runs"), layer.scope, draft.runs);
  return draft;
}
