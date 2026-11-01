// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { TextAtlas } from "./atlas";
import { encodeSections, fakeGlyphs } from "./fixtures";
import { decodeInstructions } from "./instructions";
import { LabelLayout, drawLabels, drawTexts } from "./labels";
import { Kind, QUAD_FLOATS, QuadWriter } from "./quads";

/** One 30 px run inking 7 above and 2 below its baseline, in a font of ascent 8 and descent 2.5. */
function layout(...runs: boolean[]): LabelLayout {
  const label = new LabelLayout();
  for (const shown of runs.length ? runs : [true]) label.add(shown, 30, 0, -7, 30, 2, 8, 2.5);
  return label;
}

const ANCHORS = [
  [0, 0],
  [0.5, 0],
  [1, 0],
  [0, 0.5],
  [0.5, 0.5],
  [1, 0.5],
  [0, 1],
  [0.5, 1],
  [1, 1],
  [0.5, 0],
  [0.5, 1],
  [0, 0.5],
  [1, 0.5],
];

describe("LabelLayout", () => {
  it("puts each of the 13 anchors at the point, padded, on whole pixels", () => {
    ANCHORS.forEach(([across, down], anchor) => {
      const label = layout();
      expect(label.place(100, 50, anchor, 4, 2, 0, 1, false)).toBe(true);
      // Ink 30 wide plus 4 a side; ascent 8 and descent 2.5 plus 2 a side, rounded up.
      expect([label.width, label.height]).toEqual([38, 15]);
      expect([label.x, label.y]).toEqual([100 - across * 38, Math.round(50 - down * 15)]);
      expect([label.innerX, label.baseline]).toEqual([label.x + 4, label.y + 10]);
    });
  });

  it("grows by the padding, rounds to device pixels and lets plain text sit on its ink", () => {
    const unpadded = layout();
    unpadded.place(0, 0, 0, 0, 0, 0, 1, false);
    expect([unpadded.width, unpadded.height]).toEqual([30, 11]);

    const dense = layout();
    dense.place(10, 10, 4, 4, 2, 0, 1.5, false);
    const devicePixels = [dense.width, dense.height, dense.x, dense.y].map((value) => value * 1.5);
    devicePixels.forEach((value) => expect(value).toBeCloseTo(Math.round(value), 9));
    expect(devicePixels.slice(0, 2).map(Math.round)).toEqual([57, 22]);

    const plain = layout();
    plain.place(100, 50, 7, 0, 0, 0, 1, true);
    expect([plain.height, plain.y + plain.height, plain.baseline]).toEqual([9, 50, 48]);
  });

  it("spaces shown runs by the gap and gives hidden runs no room", () => {
    const label = layout(true, false, true);
    label.place(0, 0, 0, 1, 1, 5, 1, false);
    expect([label.offset(0), label.offset(2), label.width]).toEqual([0, 35, 67]);
    expect(layout(false).place(0, 0, 0, 0, 0, 0, 1, false)).toBe(false);
  });
});

describe("drawTexts and drawLabels", () => {
  const target = (atlas: TextAtlas) => ({ width: 200, height: 100, scale: 100, dpr: 1, atlas });
  const kinds = (out: QuadWriter) => {
    const quads = out.take();
    return Array.from(
      { length: quads.length / QUAD_FLOATS },
      (_, quad) => quads[quad * QUAD_FLOATS + 9],
    );
  };

  it("draws a label as background, bar track and fill, then glyphs, and skips broken rows", () => {
    const d = decodeInstructions(
      encodeSections({
        labelStyles: [
          { size: 0.1, background: [0, 0, 0, 200], paddingX: 0.02, paddingY: 0.02, gap: 0.04 },
        ],
        labels: [
          { x: 0.5, y: 0.5, style: 0, runs: [{ text: "ab" }, { bar: 0.5 }] },
          { x: 0.5, y: 0.5, style: 0, runs: [{ bar: 0 }, { text: "" }] },
          { x: 0.5, y: 0.5, style: 3, runs: [{ text: "lost" }] },
        ],
      }),
    );
    const out = new QuadWriter();
    drawLabels(d, target(new TextAtlas(fakeGlyphs())), out);
    const { rect, glyph } = Kind;
    expect(kinds(out)).toEqual([rect, rect, rect, glyph, glyph, rect, rect]);
  });

  it("draws a text's glyphs on its anchor by their ink, spaces inking nothing", () => {
    const d = decodeInstructions(
      encodeSections({
        paints: [{ pen: [255, 0, 0], fontSize: 0.1 }],
        texts: [{ x: 0.5, y: 0.5, text: "a b", anchor: 7, paint: 0 }],
      }),
    );
    const out = new QuadWriter();
    drawTexts(d, target(new TextAtlas(fakeGlyphs())), out);
    const quads = out.take();
    expect(quads.length).toBe(2 * QUAD_FLOATS);
    // 18 wide, ink 7 above and 2 below the baseline: bottom-center at (100, 50).
    expect([...quads.subarray(0, 2), ...quads.subarray(4, 6)]).toEqual([91, 41, 6, 9]);
    expect(quads[QUAD_FLOATS]).toBe(103);
  });
});
