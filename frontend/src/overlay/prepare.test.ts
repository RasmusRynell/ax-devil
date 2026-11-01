// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { TextAtlas } from "./atlas";
import { encodeSections, fakeGlyphs, type DrawingRows } from "./fixtures";
import { decodeInstructions } from "./instructions";
import { prepareDrawing } from "./prepare";
import { DASH_PERIOD, Kind, QUAD_FLOATS } from "./quads";

const TARGET = { width: 200, height: 100 };
const prepare = (rows: DrawingRows) =>
  prepareDrawing(decodeInstructions(encodeSections(rows)), TARGET, 1, new TextAtlas(fakeGlyphs()));
/** Field `field` of every quad. */
const fields = (quads: Float32Array, field: number) =>
  Array.from(
    { length: quads.length / QUAD_FLOATS },
    (_, quad) => quads[quad * QUAD_FLOATS + field],
  );

describe("prepareDrawing", () => {
  it("makes one quad per drawable shape, with the stroke centred on the outline", () => {
    const drawing = prepare({
      paints: [
        { pen: [0, 255, 0], penWidth: 0.02, dashed: true, radius: 0.05 },
        { penWidth: 0.01 },
      ],
      boxes: [
        [0.25, 0.5, 0.25, 0.25, 0],
        [0.1, 0.1, 0, 0.5, 0],
        [Number.NaN, 0.1, 0.1, 0.1, 0],
        [0.1, 0.1, 0.1, 0.1, 9],
      ],
      circles: [
        [0.5, 0.5, 0.1, 1],
        [0.5, 0.5, 0, 1],
      ],
      points: [[0.5, 0.5, 1]],
      lines: [
        [0, 0, 0.5, 0, 1],
        [0.2, 0.2, 0.2, 0.2, 1],
      ],
    });
    expect(fields(drawing.shapes, 9)).toEqual([
      Kind.rect,
      Kind.ellipse,
      Kind.rect,
      Kind.line,
      Kind.rect,
    ]);
    // The box at (50, 50) 50 by 25 grows by half its 2 px stroke; the dash period is six strokes.
    expect([...drawing.shapes.subarray(0, 2), ...drawing.shapes.subarray(4, 9)]).toEqual([
      49,
      49,
      52,
      27,
      6,
      2,
      2 * DASH_PERIOD,
    ]);
    // The line runs 100 px with square caps: half a stroke beyond each end.
    const line = drawing.shapes.subarray(3 * QUAD_FLOATS);
    expect([...line.subarray(0, 6)]).toEqual([-0.5, -0.5, 1, 0, 101, 1]);
    expect([drawing.texts.length, drawing.polygons.length]).toEqual([0, 0]);
  });

  it("draws paths as round-capped segments whose dashes run on, and fills polygons that have a brush", () => {
    const drawing = prepare({
      paints: [{ penWidth: 0.02, dashed: true, brush: [255, 0, 0, 128] }, { penWidth: 0.02 }],
      polylines: [
        {
          points: [
            [0, 0],
            [0.5, 0],
            [0.5, 1],
          ],
          paint: 1,
        },
        { points: [[0, 0]], paint: 1 },
      ],
      polygons: [
        {
          points: [
            [0, 0],
            [0.5, 0],
            [0.5, 0.5],
          ],
          paint: 0,
        },
        {
          points: [
            [0, 0],
            [0.5, 0],
            [0.5, 0.5],
          ],
          paint: 1,
        },
      ],
    });
    expect(fields(drawing.shapes, 9)).toEqual(Array(8).fill(Kind.line));
    // Cap radius is half the stroke; the second segment starts 100 px along the dash pattern.
    expect(fields(drawing.shapes, 6).slice(0, 2)).toEqual([1, 1]);
    expect(fields(drawing.shapes, 7).slice(0, 2)).toEqual([-1, 99]);
    // One fill block: three corners, a premultiplied half red, the bounds, then the corners in pixels.
    const words = new Uint32Array(drawing.polygons.buffer);
    expect(drawing.polygons.length).toBe(12);
    expect([drawing.polygons[0], words[1], ...drawing.polygons.subarray(2, 8)]).toEqual([
      3,
      ((128 << 24) | 128) >>> 0,
      0,
      0,
      100,
      50,
      0,
      0,
    ]);
  });

  it("keeps up with a crowded frame: 650 tracked boxes, id tabs and score bars", () => {
    const boxes: [number, number, number, number, number][] = [];
    const labels: NonNullable<DrawingRows["labels"]> = [];
    for (let row = 0; row < 650; row++) {
      boxes.push([(row % 26) / 26, Math.floor(row / 26) / 25, 0.03, 0.03, 0]);
      labels.push({
        x: (row % 26) / 26,
        y: 0,
        anchor: 6,
        style: 0,
        runs: [{ text: `${row}` }, { bar: 0.97 }],
      });
    }
    const drawing = prepare({
      paints: [{ pen: [39, 174, 96], penWidth: 0.002 }],
      boxes,
      labelStyles: [
        {
          size: 0.02,
          background: [39, 174, 96, 255],
          paddingX: 0.004,
          paddingY: 0.002,
          gap: 0.004,
        },
      ],
      labels,
    });
    expect(drawing.shapes.length / QUAD_FLOATS).toBe(650);
    const glyphs = labels.reduce((sum, label) => sum + (label.runs[0].text ?? "").length, 0);
    expect(drawing.texts.length / QUAD_FLOATS).toBe(650 * 3 + glyphs);
  });

  it("draws nothing for a target without size", () => {
    const rows: DrawingRows = { paints: [{ penWidth: 0.1 }], boxes: [[0, 0, 1, 1, 0]] };
    const sections = decodeInstructions(encodeSections(rows));
    const drawing = prepareDrawing(
      sections,
      { width: 0, height: 100 },
      1,
      new TextAtlas(fakeGlyphs()),
    );
    expect(drawing.shapes.length).toBe(0);
  });
});
