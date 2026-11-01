// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { OVERLAY_TAG_BOXES, OVERLAY_TAG_STRINGS } from "../generated/api";
import { EnvelopeError } from "../playback/protocol";
import { encodeSections } from "./fixtures";
import { NO_STRING, decodeInstructions } from "./instructions";

describe("decodeInstructions", () => {
  it("views every table of the sections, resolves strings and skips unknown tags", () => {
    const d = decodeInstructions(
      encodeSections({
        paints: [
          {
            pen: [1, 2, 3],
            dashed: true,
            penWidth: 0.5,
            brush: [4, 5, 6, 7],
            fontFamily: "Mono",
            fontSize: 0.25,
          },
        ],
        boxes: [[0.25, 0.5, 0.125, 0.0625, 0]],
        polygons: [
          {
            points: [
              [0, 0],
              [1, 0],
              [0, 1],
            ],
            paint: 0,
          },
        ],
        texts: [{ x: 0.5, y: 0.5, text: "ännu", anchor: 4, paint: 0 }],
        labelStyles: [{ size: 0.125, background: [9, 9, 9, 200], gap: 0.5 }],
        labels: [
          {
            x: 0,
            y: 1,
            anchor: 6,
            style: 0,
            runs: [
              { text: "id 7", weight: 3 },
              { bar: 0.5, rgb: [10, 20, 30] },
            ],
          },
        ],
      }),
    );
    expect([...d.paints.u8.subarray(0, 4)]).toEqual([1, 2, 3, 1]);
    expect([d.paints.f32[1], d.paints.f32[4]]).toEqual([0.5, 0.25]);
    expect([...d.paints.u8.subarray(8, 12)]).toEqual([4, 5, 6, 7]);
    expect(d.strings[d.paints.u32[3]]).toBe("Mono");
    expect([d.boxes.count, ...d.boxes.f32.subarray(0, 4), d.boxes.u32[4]]).toEqual([
      1, 0.25, 0.5, 0.125, 0.0625, 0,
    ]);
    expect([...d.polygons.u32]).toEqual([0, 3, 0]);
    expect(d.pathPoints.count).toBe(3);
    expect(d.strings[d.texts.u32[2]]).toBe("ännu");
    expect(d.texts.u8[12]).toBe(4);
    expect([...d.labels.u32.subarray(3, 6)]).toEqual([0, 0, 2]);
    expect(d.strings[d.runs.u32[0]]).toBe("id 7");
    expect(d.runs.u8[11]).toBe(3);
    expect(d.runs.u32[3]).toBe(NO_STRING);
    expect([d.runs.f32[4], ...d.runs.u8.subarray(20, 23)]).toEqual([0.5, 10, 20, 30]);
    expect(Number.isNaN(d.runs.f32[1])).toBe(true);
    expect(d.lines.count + d.circles.count + d.points.count + d.polylines.count).toBe(0);
  });

  it("copies a section that is not word aligned", () => {
    const sections = encodeSections({ boxes: [[0.5, 0.5, 0.25, 0.25, 0]] });
    const boxes = sections.get(OVERLAY_TAG_BOXES) as DataView;
    const shifted = new Uint8Array(boxes.byteLength + 1);
    shifted.set(new Uint8Array(boxes.buffer, boxes.byteOffset, boxes.byteLength), 1);
    sections.set(OVERLAY_TAG_BOXES, new DataView(shifted.buffer, 1, boxes.byteLength));
    expect([...decodeInstructions(sections).boxes.f32.subarray(0, 4)]).toEqual([
      0.5, 0.5, 0.25, 0.25,
    ]);
  });

  it("rejects a section that is not a whole number of records and string offsets out of order", () => {
    const sections = encodeSections({ boxes: [[0, 0, 1, 1, 0]] });
    const boxes = sections.get(OVERLAY_TAG_BOXES) as DataView;
    sections.set(OVERLAY_TAG_BOXES, new DataView(boxes.buffer, boxes.byteOffset, 16));
    expect(() => decodeInstructions(sections)).toThrow(EnvelopeError);

    const named = encodeSections({ texts: [{ x: 0, y: 0, text: "a", paint: 0 }] });
    const table = named.get(OVERLAY_TAG_STRINGS) as DataView;
    table.setUint32(8, 99, true);
    expect(() => decodeInstructions(named)).toThrow(EnvelopeError);
  });
});
