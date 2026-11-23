// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { CodePoints } from "../expr/codePoints";
import { FakeText } from "./fakeSource";
import { INK, RunCache } from "./runCache";

describe("run cache", () => {
  it("lays out a run longer than 4096 code points completely", () => {
    const text = new FakeText();
    const font = text.font("", 10, 0);
    const lengths = [4096, 10_000];
    const advances = lengths.map((length) => {
      const cps = new CodePoints();
      for (let i = 0; i < length; i++) cps.push(0x61);
      const cache = new RunCache();
      cache.begin(text);
      const entry = cache.run(text, font, cache.fontId(font), cps, 0, cps.length);
      expect(cache.glyphEnd[entry] - cache.glyphFirst[entry]).toBe(length);
      return cache.ink[entry * INK + 4];
    });
    expect(advances[1] / advances[0]).toBeCloseTo(10_000 / 4096, 6);
  });
});
