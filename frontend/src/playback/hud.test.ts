import { describe, expect, it } from "vite-plus/test";
import type { FrameInfo } from "../stream/clock";
import { hudSections, linesFit, ptsOf, type HudLane } from "./hud";
import { MATCH_EXACT, MATCH_TOLERATED, type VideoPtsWire } from "../stream/protocol";

const PTS: VideoPtsWire = { time_base: [1, 12800], first_pts: 0 };
const frame = (sequenceId: number): FrameInfo => ({
  sequenceId,
  timestampUs: sequenceId * 40_000,
  periodUs: 40_000,
});
const lane = (change: Partial<HudLane> = {}): HudLane => ({
  match: null,
  pending: false,
  settings: { tolerance_ms: 50 },
  basis: "Time",
  ...change,
});
/** Every row of the sections as `label: value`. */
const rows = (sections: ReturnType<typeof hudSections>) =>
  Object.fromEntries(sections.flatMap((section) => section.rows));

describe("info HUD", () => {
  it("derives a frame's PTS from its time and the time base, and reports an exact match", () => {
    const sections = hudSections(
      frame(40),
      PTS,
      lane({ match: { sample: 3, kind: MATCH_EXACT, timeUs: 1_600_000, frame: 40 } }),
    );
    expect(sections.map((section) => section.title)).toEqual(["Timing", "Frame", "Overlay"]);
    expect(rows(sections)).toMatchObject({
      Status: "OK exact timestamp match",
      "Frame - overlay": "0",
      "Frame id": "40",
      "Frame timestamp": "1600000",
      PTS: "20480",
      "First PTS": "0",
      "PTS delta": "20480",
      "Time base": "1/12800",
      "Period after": "40000",
      "Overlay id": "40",
      "Overlay timestamp": "1600000",
      "Match type": "exact",
      "Lookup offset": "0",
    });
    expect(ptsOf({ time_base: [1, 12800], first_pts: 512 }, 1_600_000)).toBe(20992);
  });

  it("says there is no overlay, with None for what it lacks, and what a lane still waits for", () => {
    const none = rows(hudSections(frame(2), PTS, lane()));
    expect(none).toMatchObject({
      PTS: "1024",
      Status: "NO OVERLAY",
      "Frame - overlay": "None",
      "Overlay id": "None",
      "Overlay timestamp": "None",
    });
    expect(none).not.toHaveProperty("Matched timestamp");
    expect(rows(hudSections(frame(2), PTS, lane({ pending: true }))).Status).toBe(
      "WAITING for overlay data",
    );
  });

  it("flags an overlay after its frame as a violation and one before it as fine", () => {
    const at = (timeUs: number) =>
      rows(
        hudSections(
          frame(2),
          null,
          lane({ match: { sample: 0, kind: MATCH_EXACT, timeUs, frame: 2 } }),
        ),
      );
    expect(at(95_000)).toMatchObject({
      Status: "VIOLATION overlay is 15000 after frame",
      "Frame - overlay": "-15000",
    });
    expect(at(70_000).Status).toBe("OK overlay is before frame");
  });

  it("reports a tolerated match with its tolerance and offset, and no tolerance under the frame basis", () => {
    const match = { sample: 1, kind: MATCH_TOLERATED, timeUs: 70_000, frame: null };
    expect(rows(hudSections(frame(2), null, lane({ match })))).toMatchObject({
      "Match type": "tolerated",
      Tolerance: "50000",
      "Effective tolerance": "50000",
      "Fallback mode": "previous_with_tolerance",
      "Alignment basis": "timestamp",
      "Requested sequence": "2",
      "Matched sequence": "None",
      "Lookup offset": "-10000",
    });
    expect(rows(hudSections(frame(2), null, lane({ match, basis: "Frame" })))).toMatchObject({
      "Effective tolerance": "0",
      "Alignment basis": "sequence",
    });
    expect(
      rows(hudSections(frame(2), null, lane({ match, settings: { tolerance_ms: null } }))),
    ).toMatchObject({ Tolerance: "None", "Fallback mode": "exact_only" });
  });

  it("leaves out the PTS rows for a time base finer than a microsecond, and the PTS info for an image sequence", () => {
    const fine = rows(hudSections(frame(2), { time_base: [1, 90_000_000], first_pts: 7 }, lane()));
    expect(fine).toMatchObject({ "Time base": "1/90000000", "First PTS": "7" });
    expect(fine).not.toHaveProperty("PTS");
    expect(fine).not.toHaveProperty("PTS delta");
    expect(rows(hudSections(frame(2), null, lane()))).not.toHaveProperty("Time base");
  });
});

describe("info HUD height", () => {
  const at = (bottoms: number[], headings: number[] = []) =>
    bottoms.map((bottom, index) => ({ heading: headings.includes(index), bottom }));

  it("keeps the lines above the limit, and no heading without a row under it", () => {
    const lines = at([10, 20, 30, 40, 50, 60], [0, 3]);
    expect(linesFit(lines, 100)).toBe(6);
    expect(linesFit(lines, 55)).toBe(5);
    expect(linesFit(lines, 45)).toBe(3);
    expect(linesFit(lines, 5)).toBe(0);
  });
});
