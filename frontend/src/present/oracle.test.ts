// @vitest-environment node
import { readFileSync, readdirSync } from "node:fs";
import { describe, expect, it } from "vite-plus/test";
import { compareRows, rowDifferences } from "./canonical/compare";
import { ROOT, builtIn, hiddenBits, render } from "./canonical/render";

// The built-in v4 catalogs against the reference app's canonical Drawing Instructions (fixtures/oracle), the way the
// Rust oracle test compared v3: every row matched one to one, 2e-5 on numbers.

/** Rows that legitimately differ, by `catalog/file`, with the reason. Every entry is a decision to review. */
const EXCEPTIONS: readonly { file: string; kind: string; reason: string }[] = [];

interface OracleFile {
  sheet: string;
  hidden: string[];
  instructions: Record<string, unknown[]>;
}

function oracleFiles(catalog: string): string[] {
  return readdirSync(new URL(`fixtures/oracle/catalogs/${catalog}/`, ROOT)).sort();
}

function differences(catalog: string, file: string): string[] {
  const expected = JSON.parse(
    readFileSync(new URL(`fixtures/oracle/catalogs/${catalog}/${file}`, ROOT), "utf8"),
  ) as OracleFile;
  const { rows, report } = render(builtIn(catalog), expected.sheet, hiddenBits(expected.hidden));
  const found = compareRows(expected.instructions, rows);
  if (report.total() > 0) found.push(`${report.total()} marks skipped`);
  const allowed = EXCEPTIONS.filter((e) => e.file === `${catalog}/${file}`);
  return found.filter((message) => !allowed.some((e) => message.startsWith(`${e.kind}:`)));
}

/** The six built-ins, in the picker's order. */
const CATALOGS = ["standard", "tracking", "classic", "chunky", "glass", "minimal"];

describe.each(CATALOGS)("%s", (catalog) => {
  it.each(oracleFiles(catalog))("matches the reference on %s", (file) => {
    expect(differences(catalog, file)).toEqual([]);
  });
});

describe("decoded MOT scenes", () => {
  it.each(CATALOGS)("%s draws every scene without skipping a mark", (catalog) => {
    const compiled = builtIn(catalog);
    for (const item of readdirSync(new URL("fixtures/oracle/items/", ROOT)).filter((f) =>
      f.startsWith("mot_"),
    )) {
      const { rows, report } = render(compiled, item.replace(".bin", ""), 0);
      expect(report.total(), item).toBe(0);
      expect(rows.boxes.length, item).toBeGreaterThan(0);
    }
  });
});

describe("the comparison", () => {
  it("notices a row that moved, one that is missing and one that is extra", () => {
    const box = { x: 0.1, y: 0.1, w: 0.2, h: 0.2 };
    expect(rowDifferences("boxes", [box], [{ ...box, x: 0.1 + 1e-5 }])).toEqual([]);
    expect(rowDifferences("boxes", [box], [{ ...box, x: 0.1001 }])).toHaveLength(2);
    expect(rowDifferences("boxes", [box, box], [box])).toHaveLength(1);
  });
});

describe("oracle exceptions", () => {
  it("names a file that exists", () => {
    for (const { file } of EXCEPTIONS) {
      const [catalog, name] = file.split("/");
      expect(oracleFiles(catalog)).toContain(name);
    }
  });
});
