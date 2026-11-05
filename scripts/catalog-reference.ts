// Writes the text `ax-devil catalog reference` prints, from the tables the catalog compiler checks documents with
// (frontend/src/present/catalog and frontend/src/present/expr), so the names, types and bounds it lists are what the
// compiler accepts. Needs Node 24 and the repository's node_modules.
//
//   node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON scripts/catalog-reference.ts           print the reference
//   node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON scripts/catalog-reference.ts --write   regenerate the artifact
// (src/cli/catalog_reference.txt; the flag only quiets Node about the root package.json having no "type").
//
// The artifact is regenerate-only: never edit it by hand. A Rust test (src/cli/catalog_tests.rs) fails when it differs
// from what this prints, so a change to the compiler's tables ends in `--write`.
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createServer } from "vite-plus";

type Json = Record<string, any>; // oxlint-disable-line typescript/no-explicit-any -- a JSON Schema is read, not typed

const ARTIFACT = new URL("../src/cli/catalog_reference.txt", import.meta.url);
const FRONTEND = fileURLToPath(new URL("../frontend", import.meta.url));

/** What each function takes and gives. The compiler's checks are code, not data, so these are kept here and checked
 * against the compiler's function names below. */
const SIGNATURES: Readonly<Record<string, string>> = {
  if: "if(condition, then, else) -> the type of the branches",
  case: "case(value, key, result, ..., default) -> the type of the results; keys are written out",
  trim: "trim(text, count, where = 'end', suffix = '…', delimiter = '') -> text; where is 'end' or 'middle'",
  fixed: "fixed(number, digits) -> text; digits is a whole number from 0 to 100, written out",
  min: "min(a, b) -> number or length",
  max: "max(a, b) -> number or length",
  clamp: "clamp(value, low, high) -> number or length",
  alpha: "alpha(color, opacity) -> color",
  ramp: "ramp(value, stop, color, stop, color, ...) -> color; at least two pairs, stops written out and rising",
  palette: "palette(key, [color, ...]) -> color; the same key gives the same color",
  top: "top(colors) -> color; the highest-scoring color of an attribute",
  inset: "inset(box, fraction) -> box; fraction is 0 to 0.5",
  anchor: "anchor(box, 'name') -> point; name is one of the anchors below, written out",
};

/** The grammar is code too; these lines come from the tokenizer's operators and the parser's `in`. */
const SYNTAX = [
  "A JSON string is an expression; a number, true, false and null stand for themselves.",
  "operators: || && ! == != < <= > >= ?? + - * / ( ) [ ]; `??` takes its right side when its left is missing",
  "membership: class in ['human', 'car']",
  "lengths: 1.5px is a length in screen pixels; box.w, box.h, box.min, image.w, image.h, image.min are lengths",
  "attributes: attr.name or attr['odd name'], typed by how they are used",
  "relation layers also read source.<field> and target.<field> of the two ends",
];

const server = await createServer({
  root: FRONTEND,
  configFile: false,
  logLevel: "silent",
  appType: "custom",
  server: { middlewareMode: true, hmr: false, watch: null },
  optimizeDeps: { noDiscovery: true },
});
const load = async (path: string): Promise<any> => server.ssrLoadModule(`/src/present/${path}.ts`); // oxlint-disable-line typescript/no-explicit-any -- typed at the use
let text: string;
try {
  const types: typeof import("../frontend/src/present/catalog/types") = await load("catalog/types");
  const compile: typeof import("../frontend/src/present/catalog/compile") =
    await load("catalog/compile");
  const schema: typeof import("../frontend/src/present/catalog/schema") =
    await load("catalog/schema");
  const calls: typeof import("../frontend/src/present/expr/checkCall") =
    await load("expr/checkCall");
  const fields: typeof import("../frontend/src/present/expr/fields") = await load("expr/fields");
  const parse: typeof import("../frontend/src/present/expr/parse") = await load("expr/parse");
  text = reference({ types, compile, schema, calls, fields, parse });
} finally {
  await server.close();
}

if (process.argv.includes("--write")) writeFileSync(ARTIFACT, text);
else process.stdout.write(text);

interface Tables {
  types: typeof import("../frontend/src/present/catalog/types");
  compile: typeof import("../frontend/src/present/catalog/compile");
  schema: typeof import("../frontend/src/present/catalog/schema");
  calls: typeof import("../frontend/src/present/expr/checkCall");
  fields: typeof import("../frontend/src/present/expr/fields");
  parse: typeof import("../frontend/src/present/expr/parse");
}

/** A short name for what a JSON Schema property holds. */
function kind(property: Json): string {
  if (property.$ref)
    return String(property.$ref)
      .replace("#/$defs/", "")
      .replace(/^mark_.*/, "mark");
  if ("const" in property) return JSON.stringify(property.const);
  if (property.enum)
    return property.enum.map((choice: unknown) => JSON.stringify(choice)).join(" | ");
  if (property.oneOf) return [...new Set<string>(property.oneOf.map(kind))].join(" or ");
  if (property.type === "array") {
    const { minItems: low, maxItems: high } = property;
    const count =
      low === undefined
        ? ""
        : low === high
          ? ` of ${low}`
          : high === undefined
            ? ` of at least ${low}`
            : ` of ${low} to ${high}`;
    return `list${count} of ${kind(property.items)}`;
  }
  if (property.additionalProperties && property.type === "object")
    return `object of name -> ${kind(property.additionalProperties)}`;
  return Array.isArray(property.type) ? property.type.join(" | ") : String(property.type);
}

/** The keys of the object `definition`, one per line; `*` marks the required ones. */
function keys(definition: Json, skip: readonly string[] = []): string[] {
  return Object.entries<Json>(definition.properties)
    .filter(([key]) => !skip.includes(key))
    .map(([key, property]) => {
      const note = property.description ? `; ${property.description}` : "";
      const star = definition.required.includes(key) ? "*" : "";
      return `  ${key}${star}: ${kind(property)}${note}`;
    });
}

function reference({ types, compile, schema, calls, fields, parse }: Tables): string {
  const names = Object.keys(calls.CALLS);
  const signed = Object.keys(SIGNATURES);
  if (names.length !== signed.length || names.some((name) => !signed.includes(name)))
    throw new Error(
      `scripts/catalog-reference.ts: SIGNATURES must describe exactly the functions the compiler checks: ${names.join(", ")}`,
    );

  const document = schema.catalogSchema() as Json;
  const defs = document.$defs as Json;
  const expressionTypes = Object.entries<Json>(defs)
    .filter(([, definition]) => definition.$ref === "#/$defs/expression")
    .map(([name, definition]) => `  ${name}: ${definition.description}`);
  const lines = [
    "# Generated by scripts/catalog-reference.ts from the catalog compiler's tables.",
    "# Regenerate with `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON scripts/catalog-reference.ts --write`; never edit.",
    "",
    `Render catalog language, schema ${compile.CATALOG_SCHEMA}`,
    "",
    "DOCUMENT  a JSON object; * marks a required key",
    ...keys(document),
    "",
    "RULES  the last rule whose condition holds sets its variables",
    ...keys(defs.rule),
    "",
    "LAYERS  marks drawn for each entity or relation, in order; a layer is hidden with its feature",
    ...keys(defs.layer),
    "",
    'MARKS  {"mark": "<name>", ...}; * marks a required key',
    ...types.MARK_TYPES.flatMap((name) => [
      `  ${name}`,
      ...keys(defs[`mark_${name}`], ["mark"]).map((line) => `  ${line}`),
    ]),
    "",
    "PLACE  one candidate of a label or text; the first that fits is used",
    ...keys(defs.place),
    "",
    "ANCHORS  points of a box",
    `  anchor: ${Object.keys(calls.ANCHORS).join(", ")}`,
    `  place_anchor: the anchors, and "${types.BASELINE_ANCHOR}" for the first line's baseline`,
    "",
    "RUN  one part of a label: text or a bar",
    ...keys(defs.run),
    "",
    "TYPES  an expression has one of these types",
    ...expressionTypes,
    "",
    "SYNTAX",
    ...SYNTAX.map((line) => `  ${line}`),
    "",
    "FUNCTIONS",
    ...Object.values(SIGNATURES).map((line) => `  ${line}`),
    "",
    "FIELDS  what an expression reads of the row; relation layers also read relation",
    ...Object.entries(fields.FIELDS).map(
      ([name, field]) =>
        `  ${name}: ${field.type}${field.relationOnly ? " (relation layers only)" : ""}`,
    ),
    "",
    "LIMITS",
    `  expression length: ${parse.MAX_EXPRESSION_LENGTH} characters`,
    `  expression depth: ${parse.MAX_DEPTH}`,
    `  expression parts: ${parse.MAX_NODES}`,
  ];
  return `${lines.join("\n")}\n`;
}
