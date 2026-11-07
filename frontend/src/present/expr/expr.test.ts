// @vitest-environment node
import { getHeapStatistics, setFlagsFromString } from "node:v8";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vite-plus/test";
import { CONDITION, ENTITY, FILTER, RELATION, type Scope } from "./check";
import { CodePoints } from "./codePoints";
import { type Compiled } from "./compiled";
import { compileExpression } from "./compile";
import type { SampleView as Columns } from "../columns";
import { type FixtureEntity, makeSample } from "./sampleFixture";
import type { SampleView } from "./sampleShape";
import type { ExprType } from "./types";

setFlagsFromString("--expose-gc");
let collect: (() => void) | null = null;
try {
  collect = runInNewContext("gc") as () => void;
} catch {
  // This runtime cannot force a collection: the allocation test is skipped rather than guessed.
}

const RED = 0xff0000ff;
const GREEN = 0x00ff00ff;

const ENTITIES: FixtureEntity[] = [
  {
    id: "car-12345678901-abc",
    class: "car",
    score: 0.5,
    confidence: 0.25,
    motion: "moving",
    velocity: [0.1, 0],
    box: [0.1, 0.2, 0.5, 0.6],
    attrs: {
      occluded: true,
      face: 0.9,
      owner: "bob",
      vehicle_colors: [
        { rgba: RED, score: 0.2 },
        { rgba: GREEN, score: 0.9 },
      ],
    },
  },
  {
    id: "p2",
    class: "human",
    score: 0.125,
    confidence: 0.375,
    attrs: { occluded: false, owner: "al", vehicle_colors: [] },
  },
  { id: "x" },
];
const SAMPLE = makeSample(ENTITIES, [
  { kind: "has_part", source: 0, target: 1 },
  { kind: "near", source: 1, target: 2 },
]);

function compile(source: string, expected: ExprType, scope: Scope = ENTITY): Compiled {
  const result = compileExpression(source, scope, expected);
  if (!result.ok)
    throw new Error(`${source}: ${result.diagnostics.map((d) => d.message).join("; ")}`);
  result.expression.ctx.bind(SAMPLE);
  return result.expression.compiled;
}

/** The values of a scalar expression for rows 0..n-1. */
function scalars(
  source: string,
  expected: "number" | "bool" | "string" | "color",
  rows = 3,
  scope: Scope = ENTITY,
): number[] {
  const compiled = compile(source, expected, scope);
  if (!("scalar" in compiled)) throw new Error("not a scalar");
  return Array.from({ length: rows }, (_, row) => compiled.scalar.fn(row));
}

/** The texts of a text expression; null where the text is null. */
function texts(source: string, rows = 3): (string | null)[] {
  const compiled = compile(source, "text");
  if (compiled.type !== "text") throw new Error("not text");
  return Array.from({ length: rows }, (_, row) => {
    const out = new CodePoints();
    return compiled.emit(row, out)
      ? String.fromCodePoint(...out.data.subarray(0, out.length))
      : null;
  });
}

/** The decoded columns are what the language reads: this fails to type-check when they stop fitting. */
export const readsColumns = (columns: Columns): SampleView => columns;

const NaNs = Number.NaN;

describe("numbers and null", () => {
  it.each([
    ["score", [0.5, 0.125, NaNs]],
    ["score * 2 + 1", [2, 1.25, NaNs]],
    ["1 + 2 * 3 - 4 / 2", [5, 5, 5]],
    ["-score", [-0.5, -0.125, NaNs]],
    ["score / 0", [NaNs, NaNs, NaNs]],
    ["score ?? 0.5", [0.5, 0.125, 0.5]],
    ["score ?? confidence ?? 9", [0.5, 0.125, 9]],
    ["min(score, 0.3)", [0.3, 0.125, NaNs]],
    ["max(score, 0.3)", [0.5, 0.3, NaNs]],
    ["clamp(score, 0.2, 0.4)", [0.4, 0.2, NaNs]],
    ["velocity.x", [Math.fround(0.1), NaNs, NaNs]],
    ["attr.face", [0.9, NaNs, NaNs]],
    ["if(score > 0.3, 1, 2)", [1, 2, 2]],
    ["case(class, 'car', 10, 'human', 20, 0)", [10, 20, 0]],
  ])("%s", (source, want) => {
    expect(scalars(source, "number")).toEqual(want);
  });
});

describe("booleans and strings", () => {
  it.each([
    ["score > 0.2", [1, 0, 0]],
    ["score <= 0.125", [0, 1, 0]],
    ["score == null", [0, 0, 1]],
    ["score != null", [1, 1, 0]],
    ["class == 'car'", [1, 0, 0]],
    ["class != 'car'", [0, 1, 1]],
    ["class == null", [0, 0, 1]],
    ["class == 'nonexistent'", [0, 0, 0]],
    ["class in ['car', 'bus', 'nope']", [1, 0, 0]],
    ["!(class in ['car'])", [0, 1, 1]],
    ["class == 'car' || class == 'human'", [1, 1, 0]],
    ["class == 'car' && score > 0.4", [1, 0, 0]],
    ["!attr.occluded", [0, 1, 1]],
    ["attr.occluded", [1, 0, NaNs]],
    ["attr.occluded == null", [0, 0, 1]],
    ["attr.never != null", [0, 0, 0]],
    ["attr.face != null", [1, 0, 0]],
    ["attr.owner == 'bob'", [1, 0, 0]],
    ["motion_state == 'moving'", [1, 0, 0]],
    ["motion_state == 'stationary'", [0, 0, 0]],
    ["motion_state != null", [1, 0, 0]],
    ["id == 'p2'", [0, 1, 0]],
    ["id in ['x', 'p2']", [0, 1, 1]],
    ["null == null", [1, 1, 1]],
  ])("%s", (source, want) => {
    expect(scalars(source, "bool")).toEqual(want);
  });

  it("gives a literal absent from the sample an id of its own", () => {
    expect(scalars("'a' == 'b'", "bool")).toEqual([0, 0, 0]);
  });
});

describe("text", () => {
  it.each([
    ["id", ["car-12345678901-abc", "p2", "x"]],
    ["class", ["car", "human", null]],
    ["class ?? 'none'", ["car", "human", "none"]],
    ["trim(id, 10, 'middle')", ["car-1…1-abc", "p2", "x"]],
    ["trim(id, 10, 'end', '...')", ["car-123456...", "p2", "x"]],
    ["trim(id, 10, 'end', '...', '-')", ["car...", "p2", "x"]],
    ["trim(id, 100, 'end', '...', '-')", ["car...", "p2", "x"]],
    ["trim(id, 0)", ["…", "…", "…"]],
    ["trim(class, score * 10)", ["car", "h…", null]],
    ["trim(id, -1)", [null, null, null]],
    ["if(score > 0.3, 'high', 'low')", ["high", "low", "low"]],
    ["case(motion_state, 'moving', '▶', 'stationary', 'Ⅱ', '?')", ["▶", "?", "?"]],
    ["fixed(score, 2)", ["0.50", "0.12", null]],
    ["fixed(confidence, 2)", ["0.25", "0.38", null]],
    ["if(attr.occluded, 'Occluded', null)", ["Occluded", null, null]],
    ["fixed(attr.face, 1)", ["0.9", null, null]],
  ])("%s", (source, want) => {
    expect(texts(source)).toEqual(want);
  });
});

describe("fixed rounds half to even on the exact binary value, without toFixed", () => {
  function fixed(value: number, places: number): string | null {
    const sample = makeSample([{ id: "a", score: value }]);
    const result = compileExpression(`fixed(score, ${places})`, ENTITY, "text");
    if (!result.ok || result.expression.compiled.type !== "text") throw new Error("compile");
    result.expression.ctx.bind(sample);
    const out = new CodePoints();
    return result.expression.compiled.emit(0, out)
      ? String.fromCodePoint(...out.data.subarray(0, out.length))
      : null;
  }

  it.each([
    [0.125, 2, "0.12"],
    [0.375, 2, "0.38"],
    [2.5, 0, "2"],
    [3.5, 0, "4"],
    [5.3, 2, "5.30"],
    [123.456, 2, "123.46"],
    [1e-7, 2, "0.00"],
    [-1e-7, 2, "-0.00"],
    [-0.5, 0, "-0"],
    [0.005, 2, "0.01"],
    [1.005, 2, "1.00"],
    [0, 3, "0.000"],
    [1e21, 2, "1000000000000000000000.00"],
    [123456789012345680000, 0, "123456789012345683968"],
    [0.1, 25, "0.1000000000000000055511151"],
    [5e-324, 3, "0.000"],
    [1.7976931348623157e308, 0, BigInt(1.7976931348623157e308).toString()],
  ])("%d to %d places is %s", (value, places, want) => {
    expect(fixed(value, places)).toBe(want);
  });

  it("is not defined for infinities", () => {
    expect(fixed(Infinity, 2)).toBeNull();
  });
});

describe("colors", () => {
  it.each([
    ["#ff000080", [0xff000080, 0xff000080, 0xff000080]],
    ["#112233", [0x112233ff, 0x112233ff, 0x112233ff]],
    ["alpha(#ff0000, 15)", [0xff00000f, 0xff00000f, 0xff00000f]],
    ["alpha(#ff0000, 300)", [0xff0000ff, 0xff0000ff, 0xff0000ff]],
    ["alpha(#ff0000, score * 100)", [0xff000032, 0xff00000d, NaNs]],
    ["top(attr.vehicle_colors)", [GREEN, NaNs, NaNs]],
    ["ramp(score, 0, #000000, 1, #ffffff)", [0x808080ff, 0x202020ff, NaNs]],
    ["ramp(2, 0, #000000, 1, #ffffff)", [0xffffffff, 0xffffffff, 0xffffffff]],
    ["ramp(0.5, 0, #ff0000, 0.25, #00ff00, 0.75, #0000ff)", [0x008080ff, 0x008080ff, 0x008080ff]],
  ])("%s", (source, want) => {
    expect(scalars(source, "color")).toEqual(want);
  });

  it("picks palette colors by the CRC-32 of the id like the v3 evaluator", () => {
    const palette = [0x111111ff, 0x222222ff, 0x333333ff, 0x444444ff, 0x555555ff];
    const got = scalars("palette(id, [#111111, #222222, #333333, #444444, #555555])", "color");
    const crc = (id: string) =>
      SAMPLE.strings.crc32(SAMPLE.entityId[ENTITIES.findIndex((e) => e.id === id)]);
    expect(got).toEqual(ENTITIES.map((entity) => palette[crc(entity.id) % 5]));
    // The CRC-32 check value of "123456789" is 0xcbf43926.
    expect(makeSample([{ id: "123456789" }]).strings.crc32(3)).toBe(0xcbf43926);
  });
});

describe("lengths carry px, k, basis and bounds", () => {
  function length(source: string, scope: Scope = ENTITY) {
    const compiled = compile(source, "length", scope);
    if (compiled.type !== "length") throw new Error("not a length");
    const { px, k, lo, hi, basis } = compiled.length;
    return { px: px.fn(0), k: k.fn(0), lo: lo.fn(0), hi: hi.fn(0), basis };
  }
  const OPEN = { lo: -Infinity, hi: Infinity };

  it.each([
    ["1.5px", { px: 1.5, k: 0, ...OPEN, basis: 0 }],
    ["box.min", { px: 0, k: 1, ...OPEN, basis: 3 }],
    ["2px + box.h * 0.5 - 1px", { px: 1, k: 0.5, ...OPEN, basis: 2 }],
    ["-box.w", { px: -0, k: -1, ...OPEN, basis: 1 }],
    ["clamp(box.min * 0.15, 5px, 16px)", { px: 0, k: 0.15, lo: 5, hi: 16, basis: 3 }],
    [
      "max(clamp(box.min * 0.15, 5px, 16px) * 0.2, 2px)",
      { px: 0, k: 0.03, lo: 2, hi: 3.2, basis: 3 },
    ],
    ["min(box.h * 0.35, 20px)", { px: 0, k: 0.35, lo: -Infinity, hi: 20, basis: 2 }],
    ["clamp(box.min, 2px, 8px) / -2", { px: -0, k: -0.5, lo: -4, hi: -1, basis: 3 }],
    ["image.w * 0.01 + 3px", { px: 3, k: 0.01, ...OPEN, basis: 4 }],
  ])("%s", (source, want) => {
    const got = length(source);
    expect(got.basis).toBe(want.basis);
    for (const key of ["px", "k", "lo", "hi"] as const)
      expect(got[key]).toBeCloseTo(want[key] as number, 12);
  });
});

describe("boxes and points", () => {
  it("insets a box by a fraction and finds anchors", () => {
    const inset = compile("inset(box, 0.25)", "box");
    if (inset.type !== "box") throw new Error("box");
    expect(
      [inset.box.l(0), inset.box.t(0), inset.box.r(0), inset.box.b(0)].map((v) => +v.toFixed(5)),
    ).toEqual([0.2, 0.3, 0.4, 0.5]);
    const none = compile("inset(box, 0.75)", "box");
    if (none.type !== "box") throw new Error("box");
    expect(none.box.l(0)).toBeNaN();
    const point = compile("anchor(box, 'bottom-right')", "point");
    if (point.type !== "point") throw new Error("point");
    expect([point.x(0), point.y(0)].map((v) => +v.toFixed(5))).toEqual([0.5, 0.6]);
  });

  it("reads both ends of a relation, and is null for an end without a row", () => {
    const kinds = scalars(
      "relation == 'has_part' && source.class == 'car' && target.class == 'human'",
      "bool",
      2,
      RELATION,
    );
    expect(kinds).toEqual([1, 0]);
    const x = compile("anchor(source.box, 'center')", "point", RELATION);
    if (x.type !== "point") throw new Error("point");
    expect(+x.x(0).toFixed(5)).toBe(0.3);
  });
});

describe("retained memory", () => {
  it("evaluates compiled expressions over 10,000 rows without keeping anything", () => {
    const rows = 10000;
    const big = makeSample(
      Array.from({ length: rows }, (_, i) => ({
        id: `id-${i}`,
        class: i % 2 ? "car" : "human",
        score: (i % 100) / 100,
        attrs: { occluded: i % 3 === 0 },
      })),
    );
    const out = new CodePoints();
    const jobs: (() => number)[] = [];
    for (const [source, type] of [
      ["score ?? 0.5", "number"],
      ["if(class == 'car' && score > 0.2, score * 2, 0)", "number"],
      ["alpha(ramp(score, 0, #8c0f0f, 0.5, #ffa500, 1, #14ff78), 220)", "color"],
      ["palette(id, [#111111, #222222, #333333])", "color"],
      ["class in ['car', 'bus'] && !attr.occluded", "bool"],
      ["trim(id, 10, 'middle')", "text"],
      ["fixed(score, 2)", "text"],
    ] as const) {
      const result = compileExpression(source, ENTITY, type);
      if (!result.ok) throw new Error(source);
      result.expression.ctx.bind(big);
      const compiled = result.expression.compiled;
      if ("scalar" in compiled) {
        const read = compiled.scalar.fn;
        jobs.push(() => {
          let sum = 0;
          for (let row = 0; row < rows; row++) sum += read(row) || 0;
          return sum;
        });
      } else if (compiled.type === "text") {
        const emit = compiled.emit;
        jobs.push(() => {
          let sum = 0;
          for (let row = 0; row < rows; row++) {
            out.clear();
            sum += emit(row, out) ? out.length : 0;
          }
          return sum;
        });
      }
    }
    if (!collect) return;
    for (let i = 0; i < 10; i++) for (const job of jobs) job();
    collect();
    const before = getHeapStatistics().used_heap_size;
    for (let i = 0; i < 50; i++) for (const job of jobs) job();
    collect();
    // Nothing is kept per row or per pass: 3.5 million evaluations leave the heap where it was (V8 may still box a
    // double result per call; that is collected garbage, measured by the benchmark).
    expect(getHeapStatistics().used_heap_size - before).toBeLessThan(512 * 1024);
  });
});

describe("what each scope allows", () => {
  it.each([
    ["conditions have no lengths", CONDITION, "box.w > 1px", "bool"],
    ["filters have no lengths", FILTER, "box.w", "number"],
  ] as const)("%s", (_name, scope, source, expected) => {
    expect(compileExpression(source, scope, expected).ok).toBe(false);
  });
});
