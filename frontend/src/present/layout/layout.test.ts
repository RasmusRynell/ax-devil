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
