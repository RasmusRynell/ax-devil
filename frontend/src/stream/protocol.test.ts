// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vite-plus/test";
import { readSample } from "../present/columns";
import { EMPTY, item, mapBytes, NONE, sampleBytes } from "./fakeData";
import {
  EnvelopeError,
  ENVELOPE_MAGIC,
  frameBytes,
  parseEnvelope,
  SAMPLE_NO_FRAME,
} from "./protocol";
import { decode } from "./wire";

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

  it("parses a Sample Item without copying it", () => {
    const message = sampleBytes({ source: 2, dataRevision: 7, sample: 42, item: item("classes") });
    const parsed = parseEnvelope(message);
    if (parsed?.kind !== "sample") throw new Error("expected a sample");
    expect(parsed).toMatchObject({ source: 2, dataRevision: 7, sample: 42 });
    expect(parsed.item.buffer).toBe(message);
    expect(parsed.item.byteOffset).toBe(40);
    expect(readSample(parsed.item).rows).toBeGreaterThan(0);
  });

  it("parses a Frame Map run: sample indices, none and empty, and how each was found", () => {
    const parsed = parseEnvelope(
      mapBytes({
        lane: 2,
        policyRevision: 3,
        dataRevision: 7,
        first: 256,
        cells: [0, 0, NONE, EMPTY, 9],
        kinds: [0, 1, 0, 0, 2],
      }),
    );
    if (parsed?.kind !== "map") throw new Error("expected a frame map");
    expect(parsed).toMatchObject({ lane: 2, policyRevision: 3, dataRevision: 7, first: 256 });
    expect([...parsed.samples]).toEqual([0, 0, -1, -2, 9]);
    expect([...parsed.kinds]).toEqual([0, 1, 0, 0, 2]);
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
    const oddItem = sampleBytes().slice(0, 45);
    const shortHeader = sampleBytes().slice(0, 30);
    const cells = (count: number) => Array.from({ length: count }, () => 0);
    const emptyRun = mapBytes({ cells: [] });
    const longRun = mapBytes({ cells: cells(257) });
    const crossing = mapBytes({ first: 255, cells: cells(2) });
    const wrongLength = mapBytes({ cells: cells(3) }).slice(0, 60);
    for (const message of [
      new ArrayBuffer(8),
      wrongMagic.buffer,
      wrongVersion.buffer,
      truncated,
      oddItem,
      shortHeader,
      emptyRun,
      longRun,
      crossing,
      wrongLength,
    ]) {
      expect(() => parseEnvelope(message)).toThrow(EnvelopeError);
    }
  });
});

describe("the backend's envelopes", () => {
  // Written by the Rust encoder (`UPDATE_GOLDENS=1 make test-backend TEST=envelope_goldens`).
  const golden = (name: string): ArrayBuffer => {
    const bytes = readFileSync(new URL(`../../../fixtures/wire/${name}`, import.meta.url));
    return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  };

  it("reads the Sample the backend wrote, its item in place and byte for byte", () => {
    const message = golden("sample.bin");
    const parsed = parseEnvelope(message);
    if (parsed?.kind !== "sample") throw new Error("expected a sample");
    expect(parsed).toMatchObject({
      source: 1,
      dataRevision: 7,
      sample: 42,
      timeUs: 1_400_000,
      frame: 42,
    });
    expect(parsed.item.buffer).toBe(message);
    expect(parsed.item.byteOffset).toBe(40);
    expect(parsed.item).toEqual(item("classes"));
    expect(readSample(parsed.item).rows).toBeGreaterThan(0);

    const bare = new DataView(golden("sample.bin").slice(0));
    bare.setUint32(24, 0, true);
    bare.setUint32(36, SAMPLE_NO_FRAME, true);
    expect(parseEnvelope(bare.buffer)).toMatchObject({ timeUs: null, frame: null });
  });

  it("reads the JPEG frame the backend wrote, for the socket's thread to decode", () => {
    const parsed = parseEnvelope(golden("jpeg-frame.bin"));
    if (parsed?.kind !== "jpeg") throw new Error("expected a JPEG frame");
    const { jpeg, ...identity } = parsed.frame;
    expect(identity).toEqual({
      sequenceId: 5,
      timestampUs: 1_234_567,
      keyframe: true,
      last: true,
      width: 4,
      height: 2,
    });
    expect([...jpeg]).toEqual([0xff, 0xd8, 0xff, 0xd9]);
  });

  it("reads the Frame Map run the backend wrote", () => {
    const parsed = parseEnvelope(golden("frame-map.bin"));
    if (parsed?.kind !== "map") throw new Error("expected a frame map");
    expect(parsed).toMatchObject({ lane: 2, policyRevision: 3, dataRevision: 7, first: 256 });
    expect([...parsed.samples]).toEqual([0, 0, NONE | 0, EMPTY | 0, 9]);
    expect([...parsed.kinds]).toEqual([0, 1, 0, 0, 2]);
  });
});

describe("what the socket delivers", () => {
  it("reads a control message", () => {
    expect(decode('{"type":"Indexing","progress":0.5}')).toEqual({
      type: "message",
      message: { type: "Indexing", progress: 0.5 },
    });
  });

  it("reports a message that breaks the protocol, so the stream can end", () => {
    expect(decode("not json")?.type).toBe("malformed");
    expect(decode(new ArrayBuffer(40))?.type).toBe("malformed");
    expect(decode(mapBytes({ first: 255, cells: [0, 0] }))?.type).toBe("malformed");
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
