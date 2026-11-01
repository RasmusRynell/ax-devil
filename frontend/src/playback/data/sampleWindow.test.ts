// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vite-plus/test";
import type { Direction } from "../../generated/api";
import { sampleWindow } from "./sampleWindow";

interface Case {
  name: string;
  playhead: number;
  direction: Direction;
  first: number;
  maps: number[][];
  expected: [number, number][];
}

const FIXTURE = new URL("../../../../fixtures/sample-window/cases.json", import.meta.url);
const { cases } = JSON.parse(readFileSync(FIXTURE, "utf8")) as { cases: Case[] };

describe("sampleWindow", () => {
  it("has the shared cases", () => {
    expect(cases.length).toBeGreaterThan(0);
  });

  // The Rust twin is overlays::window::tests::matches_the_shared_fixture; both read the same file.
  it.each(cases)("$name", ({ playhead, direction, first, maps, expected }) => {
    // The wire reads NONE (0xFFFF_FFFF) and EMPTY (0xFFFF_FFFE) as -1 and -2 through an Int32Array.
    const columns = maps.map((map) => new Int32Array(Uint32Array.from(map).buffer));
    expect(sampleWindow(columns, first, playhead, direction)).toEqual(expected);
  });
});
