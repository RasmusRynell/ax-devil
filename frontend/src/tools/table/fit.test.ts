import { describe, expect, it } from "vite-plus/test";
import { dragWidth, fitColumns, rememberedWidths, type Measure } from "./fit";

const measure = (natural: number, floor: number, least: number, empty = false): Measure => ({
  natural,
  floor,
  least,
  empty,
});

// ID and Score are required, Class stretches, Motion goes before Seen when room runs out.
const COLS = [
  { title: "ID" },
  { title: "Class", stretch: true },
  { title: "Score" },
  { title: "Motion", priority: 1 },
  { title: "Seen", priority: 2 },
];
const MEASURES = [
  measure(100, 80, 60),
  measure(80, 60, 40),
  measure(50, 50, 40),
  measure(70, 70, 50),
  measure(90, 90, 60),
];
const total = (widths: number[]) => widths.reduce((a, b) => a + b, 0);

describe("fitColumns", () => {
  it.each<[string, number, [string, number][], Measure[], number[], number[]]>([
    [
      "room for all: the stretch column takes the rest",
      500,
      [],
      MEASURES,
      [0, 1, 2, 3, 4],
      [100, 190, 50, 70, 90],
    ],
    ["the highest priority number goes first", 330, [], MEASURES, [0, 1, 2, 3], [100, 110, 50, 70]],
    [
      "a column with nothing in it goes before one that has something",
      330,
      [],
      [MEASURES[0], MEASURES[1], MEASURES[2], measure(70, 70, 50, true), MEASURES[4]],
      [0, 1, 2, 4],
      [100, 90, 50, 90],
    ],
    ["required columns stay and shrink to their least", 100, [], MEASURES, [0, 1, 2], [60, 40, 40]],
    [
      "a width the user gave is kept while there is room",
      500,
      [["ID", 150]],
      MEASURES,
      [0, 1, 2, 3, 4],
      [150, 140, 50, 70, 90],
    ],
    [
      "and gives way when there is not",
      300,
      [["ID", 150]],
      MEASURES,
      [0, 1, 2, 3],
      [120, 60, 50, 70],
    ],
  ])("%s", (_, available, user, measures, shown, widths) => {
    const fit = fitColumns(COLS, measures, available, new Map(user));
    expect(fit.shown).toEqual(shown);
    expect(fit.widths).toEqual(widths);
    // Whatever is shown, nothing sticks out unless even the least widths do not fit.
    expect(total(fit.widths)).toBe(
      Math.max(available, total(fit.shown.map((i) => measures[i].least))),
    );
  });

  it("brings a column back when room returns, and hides the later of two equal priorities", () => {
    const user = new Map([["ID", 150]]);
    expect(fitColumns(COLS, MEASURES, 300, user).shown).toEqual([0, 1, 2, 3]);
    expect(fitColumns(COLS, MEASURES, 500, user).shown).toEqual([0, 1, 2, 3, 4]);
    const ties = [{ title: "A" }, { title: "B", priority: 1 }, { title: "C", priority: 1 }];
    const sizes = [measure(50, 50, 40), measure(60, 60, 40), measure(60, 60, 40)];
    expect(fitColumns(ties, sizes, 120, new Map()).shown).toEqual([0, 1]);
  });
});

describe("dragWidth", () => {
  const drag = (requested: number, start = { width: 100, sized: false }) =>
    dragWidth(COLS, MEASURES, new Map(), [0, 1, 2, 3, 4], 0, requested, 500, start);

  it("applies what the other columns leave room for, and nothing when nothing changes", () => {
    expect(drag(180)).toBe(180);
    expect(drag(1000)).toBe(230);
    expect(drag(10)).toBe(60);
    expect(drag(100)).toBeNull();
    expect(drag(100, { width: 100, sized: true })).toBe(100);
  });

  it("remembers widths per table for the session", () => {
    rememberedWidths("fit-test").set("ID", 120);
    expect(rememberedWidths("fit-test").get("ID")).toBe(120);
    expect(rememberedWidths("fit-test-other").size).toBe(0);
  });
});
