import { describe, expect, it } from "vite-plus/test";
import { PixelsMessage, planesOf } from "./pixels";

describe("Pixels message", () => {
  it("lays the I420 planes out as one run the pack pass can read back whole", () => {
    // Rows are padded to four bytes: a 6 pixel row is 8 bytes, its 3 byte chroma row 4.
    const layout = planesOf("I420", 6, 4);
    expect(layout).toMatchObject({ format: 1, stride0: 8, stride1: 4, bytes: 48 });
    // Y, then U, then V, with no gap: the planes add up to the run.
    expect(layout.stride0 * 4 + 2 * layout.stride1 * 2).toBe(layout.bytes);
    expect(planesOf("Rgba", 6, 4)).toMatchObject({ format: 2, stride0: 24, stride1: 0, bytes: 96 });
  });

  it("starts with the header the backend parses, little-endian", () => {
    const message = new PixelsMessage(planesOf("I420", 6, 4));
    message.planes.fill(7);
    const bytes = message.answer(2 ** 40 + 5);
    const view = new DataView(bytes.buffer, bytes.byteOffset);
    expect([0, 4, 16, 20, 24, 28].map((at) => view.getUint32(at, true))).toEqual([
      0x58505841, 1, 6, 4, 8, 4,
    ]);
    expect(view.getBigUint64(8, true)).toBe(BigInt(2 ** 40 + 5));
    expect(bytes).toHaveLength(32 + 48);
    expect(bytes[32]).toBe(7);
  });
});
