// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vite-plus/test";
import { makeSample } from "../expr/sampleFixture";
import { compileCatalog, compileCatalogDocument } from "./compile";
import { checkCatalog } from "./check";
import { locate } from "./locate";
import { computeVars, themeIndex } from "./program";

const builtIn = (name: string): string =>
  readFileSync(new URL(`../../../../assets/catalogs/${name}.v4.json`, import.meta.url), "utf8");

function compiled(text: string) {
  const result = compileCatalog(text);
  if (!result.ok)
    throw new Error(result.diagnostics.map((d) => `${d.pointer}: ${d.message}`).join("\n"));
  return result.catalog;
}

describe("built-in v4 catalogs", () => {
  it.each(["standard", "classic", "glass"])("%s compiles without a diagnostic", (name) => {
    const result = compileCatalog(builtIn(name));
    expect(result.ok ? [] : result.diagnostics).toEqual([]);
  });
});

describe("rules and variables", () => {
  const SAMPLE = makeSample([
    { id: "c", class: "car" },
    { id: "h", class: "human" },
    { id: "d", class: "dog" },
    { id: "n" },
    { id: "f", class: "head" },
  ]);
  const colors = (text: string, theme: string | null = null): number[] => {
    const catalog = compiled(text);
    catalog.ctx.bind(SAMPLE);
    const stroke = catalog.layers[0].marks[0].fields.stroke;
    if (stroke?.type !== "color") throw new Error("stroke");
    return Array.from({ length: SAMPLE.rows }, (_, row) => {
      computeVars(catalog, row, themeIndex(catalog, theme));
      return stroke.scalar.fn(row);
    });
  };

  it("takes the colour of the last matching rule, else the default (Standard)", () => {
    expect(colors(builtIn("standard"))).toEqual([
      0x2f80edff, 0xf2a100ff, 0x27ae60ff, 0x828b96ff, 0x9b51e0ff,
    ]);
  });

  it("lets a theme replace a default but not a rule", () => {
    const text = JSON.stringify({
      schema: 4,
      name: "T",
      description: "d",
      vars: { color: "#111111" },
      themes: { dark: { color: "#222222" } },
      rules: [
        { when: "class == 'car'", set: { color: "#333333" } },
        { when: "class != null", set: { color: "#444444" } },
      ],
      layers: [{ marks: [{ mark: "box", stroke: "color" }] }],
    });
    expect(colors(text)).toEqual([0x444444ff, 0x444444ff, 0x444444ff, 0x111111ff, 0x444444ff]);
    expect(colors(text, "dark")[3]).toBe(0x222222ff);
  });

  it("compares entity ids and motion states held in variables with literals", () => {
    const sample = makeSample([
      { id: "c", motion: "moving" },
      { id: "p", motion: "stationary" },
      { id: "moving" },
      { id: "n", motion: "unknown" },
    ]);
    const catalog = compiled(
      JSON.stringify({
        schema: 4,
        name: "T",
        description: "d",
        vars: { who: null, move: null },
        rules: [{ when: "id != null", set: { who: "id", move: "motion_state" } }],
        layers: [
          {
            marks: [
              {
                mark: "box",
                stroke:
                  "case(who, 'c', if(move == 'moving', #111111, #999999), 'p', if(move in ['x', 'stationary'], #222222, #999999), 'moving', if(move == null, #333333, #999999), #444444)",
              },
            ],
          },
        ],
      }),
    );
    catalog.ctx.bind(sample);
    const stroke = catalog.layers[0].marks[0].fields.stroke;
    if (stroke?.type !== "color") throw new Error("stroke");
    const got = [0, 1, 2, 3].map((row) => (computeVars(catalog, row, 0), stroke.scalar.fn(row)));
    expect(got).toEqual([0x111111ff, 0x222222ff, 0x333333ff, 0x444444ff]);
  });

  it("keeps a string variable comparable where it is also displayed as text", () => {
    const result = compileCatalogDocument({
      schema: 4,
      name: "T",
      description: "d",
      vars: { v: "'foo'" },
      layers: [
        {
          when: "v == 'foo'",
          marks: [
            {
              mark: "text",
              text: "v",
              color: "#ffffff",
              size: "10px",
              place: [{ at: "top-left", anchor: "top-left" }],
            },
          ],
        },
      ],
    });
    expect(result.ok ? [] : result.diagnostics).toEqual([]);
  });

  it("keeps text, lengths and strings in variables", () => {
    const text = JSON.stringify({
      schema: 4,
      name: "T",
      description: "d",
      vars: { tag: null, line: "1px", kind: null },
      rules: [
        {
          when: "class == 'car'",
          set: { tag: "trim(id, 3)", line: "box.min * 0.1", kind: "class" },
        },
      ],
      layers: [
        {
          marks: [
            { mark: "box", width: "line", stroke: "#ffffff" },
            {
              mark: "label",
              size: "10px",
              runs: [{ text: "tag", color: "#ffffff" }],
              place: [{ at: "top-left", anchor: "top-left" }],
              when: "kind == 'car'",
            },
          ],
        },
      ],
    });
    const catalog = compiled(text);
    catalog.ctx.bind(SAMPLE);
    const [box, label] = catalog.layers[0].marks;
    const width = box.fields.width;
    if (width?.type !== "length") throw new Error("width");
    computeVars(catalog, 0, 0);
    expect([width.length.k.fn(0), width.length.basis]).toEqual([0.1, 3]);
    computeVars(catalog, 1, 0);
    expect([width.length.px.fn(1), width.length.k.fn(1)]).toEqual([1, 0]);
    const run = label.runs[0].text;
    expect(run).not.toBeNull();
    expect(label.fields.when?.type === "bool" && label.fields.when.scalar.fn(0)).toBe(1);
  });
});

describe("colour lists drive Classic's swatches", () => {
  it("takes the best-scored colour of the attribute and a flag from a bool attribute", () => {
    const catalog = compiled(builtIn("classic"));
    const sample = makeSample([
      {
        id: "c",
        class: "car",
        score: 0.9,
        attrs: {
          vehicle_colors: [
            { rgba: 0x111111ff, score: 0.1 },
            { rgba: 0x222222ff, score: 0.7 },
          ],
        },
      },
      {
        id: "h",
        class: "human",
        score: 0.5,
        attrs: {
          carries_bag: true,
          upper_clothing_colors: [{ rgba: 0x333333ff, score: 0.5 }],
          lower_clothing_colors: [],
        },
      },
    ]);
    catalog.ctx.bind(sample);
    const marks = catalog.layers.find(
      (layer) => layer.feature === "attributes" && layer.marks[0].type === "circle",
    )?.marks;
    if (!marks) throw new Error("swatches");
    const [upper, lower, bag] = marks;
    const read = (mark: typeof upper, field: "when" | "fill", row: number): number => {
      const compiled = mark.fields[field];
      if (!compiled || !("scalar" in compiled)) throw new Error(field);
      return compiled.scalar.fn(row);
    };
    computeVars(catalog, 0, 0);
    expect([
      read(upper, "when", 0),
      read(upper, "fill", 0),
      read(lower, "when", 0),
      read(bag, "when", 0),
    ]).toEqual([1, 0x222222ff, 0, 0]);
    computeVars(catalog, 1, 0);
    expect([read(upper, "fill", 1), read(lower, "when", 1), read(bag, "when", 1)]).toEqual([
      0x333333ff, 0, 1,
    ]);
  });
});

describe("every compile error is located in the document", () => {
  const base = {
    schema: 4,
    name: "T",
    description: "d",
    vars: { x: 0 },
    rules: [],
    layers: [] as unknown[],
  };
  const rule = (when: string) => ({ rules: [{ when, set: { x: 1 } }] });
  // [what is wrong, document, code, pointer, text the position must point at, offset into that text]
  const cases: [string, object, string, string, string, number][] = [
    ["syntax", rule("class =="), "syntax", "/rules/0/when", "class ==", 8],
    [
      "unknown name",
      rule("class == 'car' && scor > 1"),
      "unknown-name",
      "/rules/0/when",
      "scor",
      0,
    ],
    ["type", rule("score + 1"), "type", "/rules/0/when", "score + 1", 0],
    ["arity", rule("min(score) > 1"), "arity", "/rules/0/when", "min(score)", 0],
    ["constant", { vars: { x: 0, t: "trim(id, 5, class)" } }, "constant", "/vars/t", "class)", 0],
    [
      "length",
      { vars: { x: 0, a: "clamp(box.min, 1px, 2px) + 1px" } },
      "length",
      "/vars/a",
      "clamp(box.min",
      0,
    ],
    [
      "ramp",
      { vars: { x: 0, c: "ramp(0.5, 1, #000000, 1, #ffffff)" } },
      "ramp",
      "/vars/c",
      "1, #ffffff",
      0,
    ],
    ["cycle", { vars: { a: "b + 1", b: "a + 1" } }, "cycle", "/vars/b", "a + 1", 0],
    ["infer", { vars: { x: 0, u: null } }, "infer", "/vars/u", "null", 0],
    ["schema", { schema: 3 }, "schema", "/schema", "3", 0],
    [
      "unknown key",
      { layers: [{ marx: 1, marks: [] }] },
      "unknown-key",
      "/layers/0/marx",
      '"marx": ',
      8,
    ],
    ["missing key", { description: undefined }, "missing-key", "", "{", 0],
    [
      "value",
      { layers: [{ feature: "idz", marks: [] }] },
      "value",
      "/layers/0/feature",
      '"idz"',
      0,
    ],
  ];

  it.each(cases)("%s", (_name, over, code, pointer, needle, offset) => {
    const text = JSON.stringify({ ...base, ...over }, null, 2);
    const result = compileCatalog(text);
    if (result.ok) throw new Error("compiled");
    const found = result.diagnostics.find((d) => d.code === code);
    expect(found).toBeDefined();
    expect(found?.pointer).toBe(pointer);
    const index = text.indexOf(needle) + offset;
    const before = text.slice(0, index);
    expect(locate(found as NonNullable<typeof found>, text)).toEqual({
      line: before.split("\n").length,
      column: index - before.lastIndexOf("\n"),
    });
  });

  it("reports every problem at once", () => {
    const result = compileCatalogDocument({
      ...base,
      rules: [
        { when: "scor > 1", set: { x: 1 } },
        { when: "class ==", set: { y: 1 } },
      ],
      layers: [{ marks: [{ mark: "box", wdth: 1 }] }],
    });
    if (result.ok) throw new Error("compiled");
    expect(result.diagnostics.map((d) => d.pointer).sort()).toEqual([
      "/layers/0/marks/0/wdth",
      "/rules/0/when",
      "/rules/1/set/y",
      "/rules/1/when",
    ]);
  });

  it("suggests the closest name", () => {
    const result = compileCatalog(JSON.stringify({ ...base, ...rule("idd == 'a'") }));
    expect(result.ok ? "" : result.diagnostics[0].message).toBe(
      "unknown name 'idd'; did you mean 'id'?",
    );
  });

  it("points into strings that contain escapes", () => {
    const text = String.raw`{"schema":4,"name":"T","description":"d","vars":{"x":0},"rules":[{"when":"'\u00e9' == \"a\" && scor","set":{"x":1}}],"layers":[]}`;
    const result = compileCatalog(text);
    if (result.ok) throw new Error("compiled");
    const found = result.diagnostics.find((d) => d.code === "unknown-name");
    expect(found?.span).toEqual([14, 18]);
    expect(locate(found as NonNullable<typeof found>, text)?.column).toBe(text.indexOf("scor") + 1);
  });
});

describe("locating in a deeply nested document", () => {
  it("skips values nested far deeper than the stack allows recursion", () => {
    const depth = 20_000;
    const text = `{"a":${"[".repeat(depth)}${"]".repeat(depth)},"b":1}`;
    expect(locate({ pointer: "/b" }, text)).toEqual({ line: 1, column: text.indexOf('"b"') + 5 });
  });
});

describe("a key repeated in an object", () => {
  const text = `{
  "schema": 4, "name": "T", "description": "d",
  "layers": [{ "marks": [{ "mark": "box", "stroke": "#111111", "stroke": "#222222" }] }]
}`;

  it("is reported at the later occurrence, and the document does not compile", () => {
    const result = compileCatalog(text);
    if (result.ok) throw new Error("compiled");
    const found = result.diagnostics.filter((d) => d.code === "duplicate-key");
    expect(found).toMatchObject([
      { pointer: "/layers/0/marks/0/stroke", message: "duplicate key 'stroke'" },
    ]);
    const later = text.indexOf('"#222222"');
    expect(locate(found[0], text)).toEqual({
      line: 3,
      column: later - text.lastIndexOf("\n", later),
    });
  });

  it("keeps the scanner's position when JSON.parse has dropped the first object", () => {
    const nested = '{"a":{"x":1,"x":2},"a":{}}';
    const result = compileCatalog(nested);
    if (result.ok) throw new Error("compiled");
    const found = result.diagnostics.find((d) => d.code === "duplicate-key");
    expect(found).toMatchObject({
      pointer: "/a/x",
      at: { line: 1, column: nested.indexOf("2") + 1 },
    });
    expect(locate(found as NonNullable<typeof found>, nested)).toEqual(found?.at);
  });

  it("is found in a hostile document in bounded time, and the document does not compile", () => {
    const depth = 6000;
    const hostile = `${'{"x":0,"x":'.repeat(depth)}0${"}".repeat(depth)}`;
    const started = performance.now();
    const result = compileCatalog(hostile);
    expect(performance.now() - started).toBeLessThan(1000);
    if (result.ok) throw new Error("compiled");
    const found = result.diagnostics.filter((d) => d.code === "duplicate-key");
    expect(found.length).toBeGreaterThan(1);
    expect(found.length).toBeLessThan(1000);
    expect(found.at(-1)?.message).toBe("more duplicate keys not listed");
  });

  it("is listed with the compiler's own problems", () => {
    const result = compileCatalog(text.replace('"mark": "box"', '"mark": "bx"'));
    if (result.ok) throw new Error("compiled");
    expect(result.diagnostics.map((d) => d.code).sort()).toEqual(["duplicate-key", "value"]);
  });
});

describe("drawing the examples", () => {
  const sheet = {
    name: "classes",
    item: new Uint8Array(
      readFileSync(new URL("../../../../fixtures/oracle/items/classes.bin", import.meta.url)),
    ),
  };
  const withLayers = (layers: unknown[]) =>
    JSON.stringify({ schema: 4, name: "T", description: "d", layers });

  it("counts the marks of a catalog that draws them", () => {
    const report = checkCatalog(builtIn("standard"), "standard.json", [sheet]);
    expect(report.ok).toBe(true);
    expect(report.text).toMatch(/^standard\.json: Standard\n {2}classes: \d+ marks\n$/);
  });

  it("reports a mark skipped on the example, with the sheet and where the mark is", () => {
    const report = checkCatalog(
      withLayers([
        {
          marks: [
            {
              mark: "text",
              text: "id",
              color: "top(attr.tint)",
              size: "10px",
              place: [{ at: "top-left", anchor: "bottom-left" }],
            },
          ],
        },
      ]),
      "t.json",
      [sheet],
    );
    expect(report.ok).toBe(false);
    expect(report.text).toMatch(
      /^Drawing the examples failed:\n {2}1:\d+ \/layers\/0\/marks\/0: classes: skipped for \d+ rows \[skipped\]\n$/,
    );
  });
});

describe("hostile documents", () => {
  const standard = JSON.parse(builtIn("standard"));
  const problems = (text: string) => {
    const result = compileCatalog(text);
    if (result.ok) throw new Error("compiled");
    return result.diagnostics;
  };

  it("reports a deeply nested rule condition where it is, instead of throwing", () => {
    const when = `${"(".repeat(1000)}true${")".repeat(1000)}`;
    const rules = [{ ...standard.rules[0], when }, ...standard.rules.slice(1)];
    expect(problems(JSON.stringify({ ...standard, rules }))).toMatchObject([
      { code: "limit", pointer: "/rules/0/when", span: [64, 65] },
    ]);
  });

  it("reports variables that read each other too deep to walk", () => {
    const vars: Record<string, string> = { ...standard.vars };
    for (let i = 500; i >= 1; i--) vars[`v${i}`] = `v${i - 1} + 1`;
    vars.v0 = "1";
    expect(problems(JSON.stringify({ ...standard, vars })).map((d) => d.code)).toContain("limit");
  });

  it("turns a failure of the compiler itself into a diagnostic", () => {
    const document = {
      ...standard,
      get layers(): never {
        throw new RangeError("boom");
      },
    };
    const result = compileCatalogDocument(document);
    expect(result).toMatchObject({ ok: false, diagnostics: [{ code: "internal" }] });
  });
});
