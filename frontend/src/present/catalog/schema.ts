import { ANCHORS } from "../expr/checkCall";
import type { ExprType } from "../expr/types";
import { CATALOG_SCHEMA } from "./compile";
import { FIELD_TYPES, LAYER, LAYER_TARGETS, MARKS, PLACE, ROOT, RULE, RUN } from "./shape";
import { FEATURES, MARK_TYPES, WEIGHTS } from "./types";

type Json = Record<string, unknown>;

const ref = (name: string): Json => ({ $ref: `#/$defs/${name}` });
const list = (items: Json, extra: Json = {}): Json => ({ type: "array", items, ...extra });

/** What each expression type looks like to an author; a JSON string is always an expression. */
const EXPRESSIONS: Readonly<Record<ExprType, string>> = {
  number: "A number: 0.5, or an expression.",
  bool: "A condition: true, or an expression.",
  string: "A string expression.",
  text: "Text: an expression such as \"trim(id, 10, 'middle')\".",
  color: "A color: '#rrggbb', '#rrggbbaa', or an expression.",
  colors: "A list of colors from an attribute.",
  length:
    "A length in screen pixels: '1.5px', or an expression with box.w, box.h, box.min, image.*.",
  box: "A box in the image: 'box', 'source.box', 'target.box', or inset(box, 0.2).",
  point: "A point in the image: anchor(box, 'center').",
};

/** Properties of marks that are not expressions, by key. */
const STATICS: Readonly<Record<string, Json>> = {
  pin: {
    ...ref("anchor"),
    description: "Place a w x h rect inside the reference box at this anchor.",
  },
  margin: { oneOf: [ref("length"), list(ref("length"), { minItems: 4, maxItems: 4 })] },
  pad: list(ref("length"), { minItems: 2, maxItems: 2, description: "Horizontal and vertical." }),
  font: { type: "string", description: "A font family." },
  place: list(ref("place"), { minItems: 1, maxItems: 8 }),
  runs: list(ref("run"), { minItems: 1 }),
};

function mark(name: (typeof MARK_TYPES)[number]): Json {
  const { fields, statics, required } = MARKS[name];
  const properties: Json = { mark: { const: name } };
  for (const field of fields) properties[field] = ref(FIELD_TYPES[field]);
  for (const key of statics) properties[key] = STATICS[key];
  return {
    type: "object",
    additionalProperties: false,
    required: ["mark", ...required],
    properties,
  };
}

/** The JSON Schema of a catalog document, built from the same tables the compiler checks documents with. */
export function catalogSchema(): Json {
  const expression = { type: ["string", "number", "boolean", "null"] };
  const shape = (keys: readonly string[], properties: Json): Json => ({
    type: "object",
    additionalProperties: false,
    required: keys,
    properties,
  });
  const defs: Json = {
    expression: { ...expression, description: "A literal, or a string holding an expression." },
    anchor: { enum: Object.keys(ANCHORS) },
    place: shape(PLACE.required, {
      when: ref("bool"),
      at: ref("anchor"),
      anchor: ref("anchor"),
      dx: ref("length"),
      dy: ref("length"),
    }),
    run: {
      ...shape(RUN.required, {
        text: ref("text"),
        bar: ref("number"),
        color: ref("color"),
        weight: { enum: WEIGHTS },
      }),
      oneOf: [{ required: ["text"] }, { required: ["bar"] }],
    },
    rule: shape(RULE.required, {
      when: ref("bool"),
      set: { type: "object", additionalProperties: ref("expression") },
    }),
    layer: shape(LAYER.required, {
      feature: { enum: FEATURES },
      for: { enum: LAYER_TARGETS, default: "entities" },
      when: ref("bool"),
      marks: list({ oneOf: MARK_TYPES.map((name) => ref(`mark_${name}`)) }),
    }),
  };
  for (const [type, description] of Object.entries(EXPRESSIONS))
    defs[type] = { ...ref("expression"), description };
  for (const name of MARK_TYPES) defs[`mark_${name}`] = mark(name);
  return {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "ax-devil render catalog",
    ...shape(ROOT.required, {
      schema: { const: CATALOG_SCHEMA },
      name: { type: "string" },
      description: { type: "string" },
      origin: { type: "string" },
      vars: {
        type: "object",
        description: "Per-row variables and their defaults.",
        propertyNames: { pattern: "^[A-Za-z_][A-Za-z0-9_]*$" },
        additionalProperties: ref("expression"),
      },
      themes: {
        type: "object",
        description: "Named sets of variable defaults, literals only.",
        additionalProperties: { type: "object", additionalProperties: ref("expression") },
      },
      rules: list(ref("rule")),
      layers: list(ref("layer")),
    }),
    $defs: defs,
  };
}
