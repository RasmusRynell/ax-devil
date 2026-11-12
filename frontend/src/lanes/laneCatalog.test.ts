// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { Catalogs } from "./catalogs";
import { catalogDocument } from "../stream/fakeData";
import { LaneCatalogs } from "./laneCatalog";

const STANDARD = "built-in:standard";
const BROKEN = '{"schema": 4}';

function setup() {
  const catalogs = new Catalogs(() => {});
  const lanes = new LaneCatalogs(catalogs, () => STANDARD);
  /** The backend's answer to a request, which a lane makes first. */
  const answer = (id: string, revision: number, document: string | null) => {
    catalogs.get(id);
    return catalogs.receive(id, revision, document, null);
  };
  return { lanes, answer };
}

describe("lane catalogs", () => {
  it("draws with the chosen catalog once it compiled", () => {
    const { lanes, answer } = setup();
    expect(lanes.resolve(0, "user:a.json")).toEqual({ pending: true });
    answer("user:a.json", 1, catalogDocument("classic"));
    const { catalog, pending, problem } = lanes.resolve(0, "user:a.json");
    expect([catalog?.name, pending, problem]).toEqual(["Classic", false, undefined]);
  });

  it("waits for the built-in default while that loads, and draws with it when the chosen catalog failed", () => {
    const { lanes, answer } = setup();
    answer("user:a.json", 1, BROKEN);
    expect(lanes.resolve(0, "user:a.json")).toMatchObject({ pending: true });
    answer(STANDARD, 1, catalogDocument());
    const broken = lanes.resolve(0, "user:a.json");
    expect(broken.catalog?.name).toBe("Standard");
    expect(broken.pending).toBe(false);
    expect(broken.problem).toContain("/");
    answer("user:b.json", 1, null);
    expect(lanes.resolve(1, "user:b.json")).toMatchObject({
      pending: false,
      problem: "There is no catalog user:b.json",
    });
  });

  it("keeps drawing with what the lane drew last when it chooses a catalog that fails", () => {
    const { lanes, answer } = setup();
    answer(STANDARD, 1, catalogDocument());
    answer("built-in:classic", 1, catalogDocument("classic"));
    lanes.resolve(0, "built-in:classic");
    answer("user:a.json", 1, BROKEN);
    expect(lanes.resolve(0, "user:a.json")).toMatchObject({ problem: expect.any(String) });
    expect(lanes.resolve(0, "user:a.json").catalog?.name).toBe("Classic");
    // Another lane never drew Classic: it falls back.
    expect(lanes.resolve(1, "user:a.json").catalog?.name).toBe("Standard");
    lanes.forget(0);
    expect(lanes.resolve(0, "user:a.json").catalog?.name).toBe("Standard");
  });

  it("keeps drawing with the last good document of the chosen catalog when a newer one fails", () => {
    const { lanes, answer } = setup();
    answer("user:a.json", 1, catalogDocument("classic"));
    const before = lanes.resolve(0, "user:a.json").catalog;
    answer("user:a.json", 2, BROKEN);
    const after = lanes.resolve(0, "user:a.json");
    expect(after.catalog).toBe(before);
    expect(after.problem).toBeDefined();
    answer("user:a.json", 3, catalogDocument("tracking"));
    const mended = lanes.resolve(0, "user:a.json");
    expect([mended.catalog?.name, mended.problem]).toEqual(["Tracking", undefined]);
  });
});
