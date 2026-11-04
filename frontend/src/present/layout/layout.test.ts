// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { compileCatalogDocument } from "../catalog/compile";
import type { CompiledCatalog } from "../catalog/types";
import { ORACLE_VIEW, builtIn, renderSample } from "../canonical/render";
import { fullSample } from "../fixtures";
import { FakeText } from "../text/fakeSource";
import { AtlasFull, type TextSource } from "../text/source";
import type { Row } from "../canonical/sink";
import { Layouter } from "./layout";
import { evaluate } from "../evaluate/evaluate";
import { EvalReport } from "../evaluate/report";
import { RowSet, filterRows } from "../filter/filter";
import { MarkBuffer } from "../marks/buffer";
import { LaneQuads } from "../quads/lane";
import { QuadSink } from "../quads/quadSink";
import { syntheticSample } from "../fixtures";
import type { Region } from "./sink";

const compile = (layers: unknown[]): CompiledCatalog => {
  const result = compileCatalogDocument({
    schema: 4,
    name: "t",
    description: "t",
    layers,
  });
  if (!result.ok)
    throw new Error(result.diagnostics.map((d) => `${d.pointer}: ${d.message}`).join("; "));
  return result.catalog;
};

const PX = 720;

describe("placement lists", () => {
  // The oracle's font: an 11px label with 1px padding is 15px tall, so above the box it needs 15 - 0.75 px of room.
  const ids = (top: number): Row => {
    const sample = fullSample([
      { id: "box", class: "car", box: [0.4, top / PX, 0.5, (top + 80) / PX] },
    ]);
    const { rows } = renderSample(builtIn("standard"), sample, 0);
    return rows.labels.find((row) => (row.runs as Row[])[0].text === "box") as Row;
  };

  it("puts the id above the box when there is room and inside when there is not", () => {
    expect(ids(17.42).anchor).toBe("bottom-left");
    expect(ids(13.46).anchor).toBe("top-left");
    expect(ids(13.46).y).toBeCloseTo((13.46 - 0.75) / PX, 4);
  });

  it("fits the label to the right edge too", () => {
    const catalog = compile([
      {
        marks: [
          {
            mark: "label",
            size: "10px",
            pad: ["2px", "2px"],
            runs: [{ text: "id", color: "#ffffff" }],
            place: [
              { at: "top-right", anchor: "top-left" },
              { at: "top-left", anchor: "top-right" },
            ],
          },
        ],
      },
    ]);
    const label = (right: number) => {
      const sample = fullSample([{ id: "wide-id", box: [right - 0.1, 0.3, right, 0.5] }]);
      return renderSample(catalog, sample, 0).rows.labels[0];
    };
    expect(label(0.5).anchor).toBe("top-left");
    expect(label(0.999).anchor).toBe("top-right");
  });

  it("uses the last candidate when none fits", () => {
    const catalog = compile([
      {
        marks: [
          {
            mark: "label",
            size: "10px",
            runs: [{ text: "id", color: "#ffffff" }],
            place: [
              { at: "top-left", anchor: "bottom-left", dy: "-200px" },
              { at: "top-left", anchor: "bottom-left", dy: "-100px" },
            ],
          },
        ],
      },
    ]);
    const sample = fullSample([{ id: "a", box: [0.3, 0.1, 0.4, 0.3] }]);
    const label = renderSample(catalog, sample, 0).rows.labels[0];
    expect(label.y).toBeCloseTo((0.1 * PX - 100) / PX, 4);
  });
});

describe("row failures", () => {
  it("leaves out the earlier layers' marks of a row whose label size is unusable, and only that row", () => {
    // The label's size follows the score, so a score of 0 fails layout after the box of layer 0 was drawn.
    const catalog = compile([
      { marks: [{ mark: "box", stroke: "#ffffff", width: "1px" }] },
      {
        marks: [
          {
            mark: "label",
            size: "score * 20px",
            runs: [{ text: "id", color: "#ffffff" }],
            place: [{ at: "top-left", anchor: "bottom-left" }],
          },
        ],
      },
    ]);
    const sample = fullSample([
      { id: "a", score: 0, box: [0.1, 0.2, 0.3, 0.4] },
      { id: "b", score: 0.5, box: [0.5, 0.2, 0.7, 0.4] },
    ]);
    const { rows, report } = renderSample(catalog, sample, 0);
    expect(rows.boxes.map((box) => box.x)).toEqual([0.5]);
    expect(rows.labels).toHaveLength(1);
    expect(Array.from(report.skipped)).toEqual([0, 1]);
  });

  it("fails a row the same whether or not its failing mark is inside the visible window", () => {
    // The label sits in the middle and has no size; only the box reaches the window on the left.
    const catalog = compile([
      { marks: [{ mark: "box", stroke: "#ffffff", width: "1px" }] },
      {
        marks: [
          {
            mark: "label",
            on: "inset(box, 0.4)",
            size: "0px",
            runs: [{ text: "id", color: "#ffffff" }],
            place: [{ at: "top-left", anchor: "bottom-left" }],
          },
        ],
      },
    ]);
    const sample = fullSample([{ id: "a", box: [0.1, 0.1, 0.9, 0.9] }]);
    const visible = { left: 0, top: 0, right: 384, bottom: 720 };
    expect(renderSample(catalog, sample, 0).rows.boxes).toHaveLength(0);
    expect(renderSample(catalog, sample, 0, { ...ORACLE_VIEW, visible }).rows.boxes).toHaveLength(
      0,
    );
  });
});

describe("units", () => {
  const catalog = compile([
    { marks: [{ mark: "box", stroke: "#ffffff", width: "4px", outset: "box.min * 0.1" }] },
  ]);
  const sample = fullSample([{ id: "a", box: [0.2, 0.2, 0.4, 0.5] }]);
  const box = (zoom: number, dpr = 1) =>
    renderSample(catalog, sample, 0, { ...ORACLE_VIEW, zoom, dpr }).rows.boxes[0];

  it("does not let the UI zoom scale lengths in px but keeps lengths relative to the box", () => {
    const plain = box(1);
    const zoomed = box(2);
    expect(zoomed.pen_width).toBeCloseTo((plain.pen_width as number) / 2, 4);
    // box.min is the box on screen, which the zoom already scaled.
    expect(zoomed.x).toBe(plain.x);
  });
});

describe("atlas pages", () => {
  it("lays the lane out again on a fresh page when the atlas fills up", () => {
    class Filling extends FakeText {
      full = true;
      resets = 0;
      override glyph(...args: Parameters<FakeText["glyph"]>): ReturnType<FakeText["glyph"]> {
        if (this.full) throw new AtlasFull();
        return super.glyph(...args);
      }
      override reset(): void {
        this.resets++;
        this.full = false;
        super.reset();
      }
    }
    const text = new Filling();
    const sample = fullSample([{ id: "a", class: "car", score: 0.5 }]);
    const { rows } = renderSample(builtIn("standard"), sample, 0, ORACLE_VIEW, text as TextSource);
    expect(text.resets).toBe(1);
    expect(rows.labels.length).toBeGreaterThan(0);
  });
});

describe("quads", () => {
  it("records where each layer ends", () => {
    const catalog = builtIn("classic");
    const sample = fullSample([
      { id: "a", class: "car", score: 0.5, motion: "moving", velocity: [0.01, 0] },
    ]);
    const set = filterRows(sample, null, { enabled: new Uint32Array(0), options: 0 }, new RowSet());
    const marks = new MarkBuffer();
    evaluate(sample, set, catalog, { hidden: 0, theme: null }, marks, new EvalReport());
    const lane = new LaneQuads();
    new Layouter(new FakeText()).run(marks, catalog, sample, ORACLE_VIEW, new QuadSink(lane));
    expect(lane.layerCount).toBe(catalog.layers.length);
    expect(lane.layerQuads[lane.layerCount - 1]).toBe(lane.quads.count);
    for (let i = 1; i < lane.layerCount; i++)
      expect(lane.layerQuads[i]).toBeGreaterThanOrEqual(lane.layerQuads[i - 1]);
    expect(lane.quads.count).toBeGreaterThan(5);
  });
});

describe("primitive quads", () => {
  type Quad = Record<"kind" | "x" | "y" | "w" | "h" | "radius" | "stroke" | "dash", number> & {
    fill: number;
    pen: number;
  };
  /** The quads a mark draws for an entity whose box is (320, 180) to (640, 360) on the 1280 x 720 oracle view. */
  const quadsOf = (mark: object, score = 0.5): Quad[] => {
    const sample = fullSample([{ id: "a", score, box: [0.25, 0.25, 0.5, 0.5] }]);
    const marks = new MarkBuffer();
    const catalog = compile([{ marks: [mark] }]);
    const set = filterRows(sample, null, { enabled: new Uint32Array(0), options: 0 }, new RowSet());
    evaluate(sample, set, catalog, { hidden: 0, theme: null }, marks, new EvalReport());
    const lane = new LaneQuads();
    new Layouter(new FakeText()).run(marks, catalog, sample, ORACLE_VIEW, new QuadSink(lane));
    const f = lane.quads.subarray();
    const u = new Uint32Array(f.buffer, f.byteOffset, f.length);
    return Array.from({ length: lane.quads.count }, (_, i) => ({
      x: f[i * 12],
      y: f[i * 12 + 1],
      w: f[i * 12 + 4],
      h: f[i * 12 + 5],
      radius: f[i * 12 + 6],
      stroke: f[i * 12 + 7],
      dash: f[i * 12 + 8],
      kind: f[i * 12 + 9],
      fill: u[i * 12 + 10],
      pen: u[i * 12 + 11],
    }));
  };
  const RED = 0xff0000ff;

  // The reference strokes inward from a quad grown by half the stroke, so ink straddles the contour (shape_quads.py).
  it("grows a box by half its stroke, clamps its corner radius on the centre line and dashes by 6 widths", () => {
    const plain = { mark: "box", stroke: "#ff0000", width: "4px" };
    expect(quadsOf(plain)).toEqual([
      { kind: 0, x: 318, y: 178, w: 324, h: 184, radius: 0, stroke: 4, dash: 0, fill: 0, pen: RED },
    ]);
    const [rounded] = quadsOf({ ...plain, radius: "10px", dash: "true" });
    expect([rounded.radius, rounded.dash]).toEqual([12, 24]);
    expect(quadsOf({ ...plain, radius: "1000px" })[0].radius).toBe(92);
  });

  it("draws a fill-only box for a zero-width stroke and premultiplies a translucent fill", () => {
    const [box] = quadsOf({ mark: "box", stroke: "#ff0000", width: "0px", fill: "#0000ff80" });
    expect(box).toMatchObject({ x: 320, y: 180, w: 320, h: 180, stroke: 0, dash: 0, pen: 0 });
    expect(box.fill).toBe(0x80800000);
  });

  it("draws a circle as the ellipse in the square around its centre and a stroke-wide square for a point", () => {
    const circle = { mark: "circle", pin: "center", r: "6px", stroke: "#ff0000", width: "2px" };
    expect(quadsOf(circle)).toEqual([
      { kind: 1, x: 473, y: 263, w: 14, h: 14, radius: 0, stroke: 2, dash: 0, fill: 0, pen: RED },
    ]);
    const centre = "anchor(box, 'center')";
    const point = { mark: "line", from: centre, to: centre, stroke: "#ff0000", width: "5px" };
    expect(quadsOf(point)).toEqual([
      { kind: 0, x: 477.5, y: 267.5, w: 5, h: 5, radius: 0, stroke: 0, dash: 0, fill: RED, pen: 0 },
    ]);
  });

  // A label of one bar run at the box's top-left, 20px tall text metrics: ascent 18.56, cap height 14.6 (FakeText).
  const meter = (score: number, color = "#ff0000") =>
    quadsOf(
      {
        mark: "label",
        size: "20px",
        pad: ["0px", "0px"],
        runs: [{ bar: "score", color }],
        place: [{ at: "top-left", anchor: "top-left" }],
      },
      score,
    );

  it("draws a meter as a pill track centred on half the cap height with a fill of the value", () => {
    // Thickness is 0.3 of the size (6px, at least 2px), length 2.5 sizes (50px). Nothing is painted behind the meter,
    // so its box is its ink and the track's top is the box's top.
    const top = 180;
    const [track, fill, ...rest] = meter(0.5);
    expect(rest).toEqual([]);
    expect(track).toMatchObject({ x: 320, w: 50, h: 6, radius: 3, stroke: 0 });
    expect(track.y).toBeCloseTo(top, 3);
    expect(track.fill >>> 24).toBe(70);
    expect(fill).toMatchObject({ x: 320, w: 25, h: 6, radius: 3, fill: RED });
    expect(fill.y).toBeCloseTo(top, 3);
  });

  it("sizes a label without a background by its ink and a filled one by the font's ascent and descent", () => {
    // 20px text: ascent 18.56, descent 4.72, glyphs 15px tall (cap height 14.6 rounded up), 2px by 3px padding.
    const label = (extra: object) =>
      quadsOf({
        mark: "label",
        size: "20px",
        pad: ["2px", "3px"],
        runs: [{ text: "'Ag'", color: "#ffffff" }],
        place: [{ at: "top-left", anchor: "top-left" }],
        ...extra,
      });
    const bare = label({});
    expect(bare).toHaveLength(2);
    expect(bare[0].y).toBe(180 + 3);
    const [rect, ...glyphs] = label({ background: "#000000" });
    expect(rect.h).toBe(Math.ceil(18.56 + 4.72 + 6));
    expect(glyphs[0].y).toBe(Math.round(180 + 3 + 18.56) - 15);
  });

  it("draws only the track for zero and a dot for the smallest value", () => {
    expect(meter(0)).toHaveLength(1);
    // 1% of 50px is 0.5px, narrower than the pill is tall.
    expect(meter(0.01)[1].w).toBe(6);
    expect(meter(1)[1].w).toBe(50);
  });

  it("scales a translucent colour's meter without moving it", () => {
    const [track, fill] = meter(0.5, "#ff000080");
    const [opaque, opaqueFill] = meter(0.5);
    expect([track.x, track.y, track.w, track.h]).toEqual([opaque.x, opaque.y, opaque.w, opaque.h]);
    expect([fill.x, fill.y, fill.w, fill.h]).toEqual([
      opaqueFill.x,
      opaqueFill.y,
      opaqueFill.w,
      opaqueFill.h,
    ]);
    expect([track.fill >>> 24, fill.fill >>> 24]).toEqual([35, 128]);
  });
});

describe("culling", () => {
  /** The quads of `catalog` over `sample` on a 4x zoomed image, laid out for `visible` or for everything. */
  const quadsOf = (catalog: CompiledCatalog, visible: Region | null): Float32Array => {
    const sample = syntheticSample(3000);
    const set = filterRows(
      sample,
      null,
      { enabled: new Uint32Array(sample.maskWords), options: 0 },
      new RowSet(),
    );
    const marks = new MarkBuffer();
    evaluate(sample, set, catalog, { hidden: 0, theme: null }, marks, new EvalReport());
    const lane = new LaneQuads();
    const view = { width: 5120, height: 2880, dpr: 1, zoom: 1, visible };
    new Layouter(new FakeText()).run(marks, catalog, sample, view, new QuadSink(lane));
    return lane.quads.subarray().slice();
  };

  it.each(["standard", "classic", "glass"])(
    "%s lays out nothing outside a 4x zoom's window and everything inside it",
    (name) => {
      const catalog = builtIn(name);
      const window: Region = { left: 1920, top: 1080, right: 3200, bottom: 1800 };
      const all = quadsOf(catalog, null);
      const culled = quadsOf(catalog, window);
      const key = (quads: Float32Array, i: number) => quads.slice(i * 12, i * 12 + 12).join(",");
      const shown = (quads: Float32Array, i: number) => {
        const [x, y, , , w, h] = quads.slice(i * 12, i * 12 + 6);
        return (
          x + w + 1 >= window.left &&
          x - 1 <= window.right &&
          y + h + 1 >= window.top &&
          y - 1 <= window.bottom
        );
      };
      const kept = new Set<string>();
      for (let i = 0; i < culled.length / 12; i++) kept.add(key(culled, i));
      let inside = 0;
      for (let i = 0; i < all.length / 12; i++) {
        // Line quads are rotated; their corners are not x, y, w, h.
        if (all[i * 12 + 9] === 2 || !shown(all, i)) continue;
        inside++;
        expect(kept.has(key(all, i))).toBe(true);
      }
      expect(inside).toBeGreaterThan(50);
      expect(culled.length).toBeLessThan(all.length / 4);
    },
  );
});
