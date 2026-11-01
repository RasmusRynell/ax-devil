// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { boxesSection, overlayBytes } from "./fakeOverlay";
import { EnvelopeError, ENVELOPE_MAGIC, frameBytes, parseEnvelope } from "./protocol";

function head(
  kind: number,
  headerLen: number,
  sequenceId = 0,
  timestampUs = 0,
  flags = 0,
): DataView {
  const view = new DataView(new ArrayBuffer(headerLen));
  view.setUint32(0, ENVELOPE_MAGIC, true);
  view.setUint8(4, 1);
  view.setUint8(5, kind);
  view.setUint16(6, headerLen, true);
  view.setBigUint64(8, BigInt(sequenceId), true);
  view.setBigInt64(16, BigInt(timestampUs), true);
  view.setUint32(24, flags, true);
  return view;
}

function videoFrame(
  layout: number,
  planes: number[][],
  sequenceId = 7,
  timestampUs = 233333,
  flags = 3,
): Uint8Array {
  const view = head(1, 64, sequenceId, timestampUs, flags);
  view.setUint32(28, 4, true);
  view.setUint32(32, 2, true);
  view.setUint8(36, layout);
  view.setUint8(37, 1);
  view.setUint8(38, 1);
  planes.forEach((plane, index) => {
    view.setUint32(40 + index * 4, plane.length / 2, true);
    view.setUint32(52 + index * 4, plane.length, true);
  });
  return Uint8Array.from([...new Uint8Array(view.buffer), ...planes.flat()]);
}

describe("envelope", () => {
  it.each([
    {
      name: "I420",
      layout: 1,
      format: "i420",
      planes: [
        [1, 2, 3, 4],
        [5, 6],
        [7, 8],
      ],
    },
    { name: "NV12", layout: 2, format: "nv12", planes: [[1, 2, 3, 4], [5, 6, 7, 8], []] },
    { name: "RGBA", layout: 3, format: "rgba", planes: [[1, 2, 3, 4, 5, 6, 7, 8], [], []] },
  ])("parses a $name video frame with its identity and planes", ({ layout, format, planes }) => {
    const parsed = parseEnvelope(videoFrame(layout, planes));
    if (parsed?.kind !== "frame") throw new Error("expected a frame");
    const { frame } = parsed;
    expect(frame).toMatchObject({
      sequenceId: 7,
      timestampUs: 233333,
      keyframe: true,
      last: true,
      width: 4,
      height: 2,
      format,
      matrix: "bt709",
      fullRange: true,
      byteLength: planes.flat().length,
    });
    expect(frame.planes.map((plane) => [...plane])).toEqual(planes);
  });

  it("parses a timeline, aligned or not", () => {
    const times = [0, 40000, 80000.5];
    const message = Uint8Array.from([
      ...new Uint8Array(head(3, 32).buffer),
      ...new Uint8Array(Float64Array.from(times).buffer),
    ]);
    const shifted = new Uint8Array(message.length + 1);
    shifted.set(message, 1);
    for (const input of [message, shifted.subarray(1)]) {
      const parsed = parseEnvelope(input);
      expect(parsed?.kind === "timeline" && [...parsed.times]).toEqual(times);
    }
  });

  it("parses an overlay's header and keeps every section, known or not, by tag", () => {
    const unknown = new Uint8Array([9, 9, 9]);
    const parsed = parseEnvelope(
      overlayBytes({
        lane: 2,
        sequenceId: 41,
        revision: 7,
        match: 3,
        opacity: 0.5,
        target: { width: 800, height: 450 },
        sampleKey: -1200,
        sections: [[64, unknown], boxesSection(2)],
      }),
    );
    if (parsed?.kind !== "overlay") throw new Error("expected an overlay");
    expect(parsed).toMatchObject({
      lane: 2,
      sequenceId: 41,
      revision: 7,
      match: "retained",
      opacity: 0.5,
      target: { width: 800, height: 450 },
      sampleKey: -1200,
    });
    expect([...parsed.sections.keys()]).toEqual([64, 2]);
    // The section after an unknown one of a length that needs padding is found where the padding ends.
    expect(parsed.sections.get(2)?.byteLength).toBe(40);
    expect(parsed.sections.get(64)?.byteLength).toBe(3);
  });

  it("ignores kinds from a newer backend", () => {
    expect(parseEnvelope(head(99, 32).buffer)).toBeNull();
  });

  it("rejects malformed messages", () => {
    const wrongMagic = head(3, 32);
    wrongMagic.setUint32(0, 0, true);
    const wrongVersion = head(3, 32);
    wrongVersion.setUint8(4, 2);
    const truncated = videoFrame(1, [
      [1, 2, 3, 4],
      [5, 6],
      [7, 8],
    ]).subarray(0, 70);
    const shortOverlay = overlayBytes({ sections: [boxesSection(2)] }).slice(0, 80);
    const unknownMatch = new Uint8Array(overlayBytes({ match: 9 }));
    for (const message of [
      new ArrayBuffer(8),
      wrongMagic.buffer,
      wrongVersion.buffer,
      truncated,
      shortOverlay,
      unknownMatch,
    ]) {
      expect(() => parseEnvelope(message)).toThrow(EnvelopeError);
    }
  });
});

describe("frame bytes", () => {
  it.each([
    { width: 16, height: 16, format: "i420", bytes: 384 },
    { width: 18, height: 6, format: "nv12", bytes: 162 },
    { width: 17, height: 5, format: "i420", bytes: 85 + 2 * 9 * 3 },
    { width: 4, height: 2, format: "rgba", bytes: 32 },
  ] as const)("is $bytes for $width x $height $format", ({ width, height, format, bytes }) => {
    expect(frameBytes(width, height, format)).toBe(bytes);
  });
});
