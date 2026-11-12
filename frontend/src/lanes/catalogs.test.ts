// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { Catalogs } from "./catalogs";
import { catalogDocument } from "../stream/fakeData";

function held() {
  const asked: string[] = [];
  return { asked, catalogs: new Catalogs((id) => asked.push(id)) };
}
const BROKEN = '{"schema": 4}';

describe("catalogs", () => {
  it("asks for a catalog once and compiles what the backend answers", () => {
    const { asked, catalogs } = held();
    expect(catalogs.get("built-in:standard")).toEqual({ state: "loading" });
    catalogs.get("built-in:standard");
    expect(asked).toEqual(["built-in:standard"]);
    expect(catalogs.receive("built-in:standard", 1, catalogDocument(), null)).toBe(true);
    expect(catalogs.get("built-in:standard")).toMatchObject({ state: "ready" });
    // An answer to a catalog nobody asked for, or the same answer again, changes nothing.
    expect(catalogs.receive("user:other.json", 1, catalogDocument(), null)).toBe(false);
    expect(catalogs.receive("built-in:standard", 1, catalogDocument(), null)).toBe(false);
  });

  it("takes any revision that differs, a lower one too, as a restarted backend counts from the start", () => {
    const { catalogs } = held();
    catalogs.get("user:a.json");
    catalogs.receive("user:a.json", 7, catalogDocument("standard"), null);
    const first = catalogs.get("user:a.json");
    expect(catalogs.receive("user:a.json", 8, catalogDocument("classic"), null)).toBe(true);
    const second = catalogs.get("user:a.json");
    expect(second).not.toBe(first);
    expect(catalogs.receive("user:a.json", 2, catalogDocument("standard"), null)).toBe(true);
    // The restarted backend reached the revision it had, with other text.
    expect(catalogs.receive("user:a.json", 2, catalogDocument("tracking"), null)).toBe(true);
  });

  it("keeps the last compile and says why when a newer document fails, has gone or cannot be served", () => {
    const { catalogs } = held();
    catalogs.get("user:a.json");
    catalogs.receive("user:a.json", 1, catalogDocument(), null);
    const good = catalogs.get("user:a.json");
    if (good.state !== "ready" || !good.catalog) throw new Error("no compile");

    catalogs.receive("user:a.json", 2, BROKEN, null);
    const failed = catalogs.get("user:a.json");
    expect(failed).toMatchObject({ state: "ready", catalog: good.catalog });
    expect(failed.state === "ready" && failed.problem).toMatch(/\(\d+:\d+\)/);

    catalogs.receive("user:a.json", 3, null, null);
    expect(catalogs.get("user:a.json")).toMatchObject({
      catalog: good.catalog,
      problem: "There is no catalog user:a.json",
    });
    catalogs.receive("user:a.json", 4, null, "The file is larger than 1 MiB");
    expect(catalogs.get("user:a.json")).toMatchObject({
      catalog: good.catalog,
      problem: "The file is larger than 1 MiB",
    });

    catalogs.receive("user:a.json", 5, catalogDocument(), null);
    const mended = catalogs.get("user:a.json");
    expect(mended.state === "ready" && mended.problem).toBeUndefined();
  });

  it("keeps the last compile when a document is hostile instead of throwing", () => {
    const { catalogs } = held();
    catalogs.get("user:a.json");
    catalogs.receive("user:a.json", 1, catalogDocument(), null);
    const good = catalogs.get("user:a.json");
    if (good.state !== "ready" || !good.catalog) throw new Error("no compile");
    const document = JSON.parse(catalogDocument());
    document.rules = [{ when: `${"(".repeat(1000)}true${")".repeat(1000)}`, set: {} }];
    expect(catalogs.receive("user:a.json", 2, JSON.stringify(document), null)).toBe(true);
    expect(catalogs.get("user:a.json")).toMatchObject({
      catalog: good.catalog,
      problem: expect.stringContaining("/rules/0/when"),
    });
  });

  it("keeps the revision it draws and exports from when the listing says the catalog is gone or failing", () => {
    const { catalogs } = held();
    const listed = { id: "user:a.json", name: "A", description: "" };
    catalogs.open([listed]);
    catalogs.get(listed.id);
    catalogs.receive(listed.id, 3, catalogDocument(), null);
    expect(catalogs.compiledRevision(listed.id)).toBe(3);
    catalogs.list([{ ...listed, problem: "not JSON" }]);
    expect(catalogs.compiledRevision(listed.id)).toBe(3);
    catalogs.list([]);
    expect(catalogs.compiledRevision(listed.id)).toBe(3);
  });

  it("asks again for every catalog it holds when a connection opens, and lists what the backend offers", () => {
    const { asked, catalogs } = held();
    catalogs.get("built-in:standard");
    catalogs.get("user:a.json");
    catalogs.receive("built-in:standard", 1, catalogDocument(), null);
    const listing = [{ id: "built-in:standard", name: "Standard", description: "" }];
    catalogs.open(listing);
    expect(asked).toEqual(["built-in:standard", "user:a.json", "built-in:standard", "user:a.json"]);
    expect(catalogs.listing).toBe(listing);
    // The answer that repeats what it holds does not compile again.
    expect(catalogs.receive("built-in:standard", 1, catalogDocument(), null)).toBe(false);
    const pushed = [...listing, { id: "user:a.json", name: "A", description: "" }];
    catalogs.list(pushed);
    expect(catalogs.listing).toBe(pushed);
  });
});
