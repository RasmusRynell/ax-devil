// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import feed from "../../../fixtures/catalogs/lend-feed.json";
import { LaneCatalogs } from "./laneCatalog";
import { Catalogs } from "./catalogs";
import { catalogDocument } from "../stream/fakeData";

afterEach(() => vi.unstubAllGlobals());

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

  it("reconciles deferred reloads and pushes without rolling back a connection or a newer action", async () => {
    vi.stubGlobal("window", { sessionStorage: { getItem: () => null } });
    for (const httpFirst of [false, true]) {
      const { catalogs } = held();
      const id = "user:a.json";
      catalogs.get(id);
      catalogs.receive(id, 1, catalogDocument());
      let reply!: (response: Response) => void;
      vi.stubGlobal(
        "fetch",
        vi.fn(
          () =>
            new Promise<Response>((resolve) => {
              reply = resolve;
            }),
        ),
      );
      const pending = catalogs.validate(id, () => true);
      if (httpFirst) {
        reply(Response.json({ revision: 2, document: catalogDocument("tracking") }));
        expect(await pending).toBe(true);
        catalogs.receive(id, 3, BROKEN);
      } else {
        const rejected = expect(pending).rejects.toThrow();
        catalogs.receive(id, 3, BROKEN);
        reply(Response.json({ revision: 2, document: catalogDocument("tracking") }));
        await rejected;
      }
      expect(catalogs.get(id)).toMatchObject({ problem: expect.any(String) });
      expect(catalogs.compiledRevision(id)).toBe(1);
      // A superseded action cannot even install a newer valid document.
      const superseded = catalogs.validate(id, () => false);
      reply(Response.json({ revision: 5, document: catalogDocument() }));
      expect(await superseded).toBe(false);
      expect(catalogs.get(id)).toMatchObject({ problem: expect.any(String) });
      const newerValid = catalogs.validate(id, () => true);
      catalogs.receive(id, 7, catalogDocument("classic"));
      reply(Response.json({ revision: 6, document: catalogDocument("tracking") }));
      expect(await newerValid).toBe(true);
      expect(catalogs.get(id)).toMatchObject({ catalog: { name: "Classic" } });
      // Reopening admits lower revisions, but invalidates old HTTP replies.
      const oldConnection = catalogs.validate(id, () => true);
      catalogs.open([]);
      catalogs.receive(id, 1, catalogDocument("classic"));
      reply(Response.json({ revision: 6, document: catalogDocument() }));
      expect(await oldConnection).toBe(false);
      expect(catalogs.get(id)).toMatchObject({ catalog: { name: "Classic" } });
      const validation = catalogs.validate(id, () => true);
      reply(Response.json({ revision: 8, document: catalogDocument("tracking") }));
      expect(await validation).toBe(true);
      expect(catalogs.compiledRevision(id)).toBe(1);
      // Identical text/revision from the feed must still promote the compile: it is now retained for export.
      catalogs.receive(id, 8, catalogDocument("tracking"));
      expect(catalogs.compiledRevision(id)).toBe(8);
      expect(catalogs.get(id)).toMatchObject({ catalog: { name: "Tracking" } });
    }
  });

  it("retains the drawing but reports withdrawal after the last Owner lend, then accepts restoration", async () => {
    vi.stubGlobal("window", { sessionStorage: { getItem: () => null } });
    const { catalogs } = held();
    const lanes = new LaneCatalogs(catalogs, () => "built-in:standard");
    const id = feed.initial.id;
    catalogs.get(id);
    const receive = (reply: typeof feed.initial | typeof feed.withdrawn) =>
      catalogs.receive(reply.id, reply.revision, reply.document, reply.problem);
    receive(feed.initial);
    const drawing = lanes.resolve(0, id).catalog;
    expect(drawing).toBeDefined();
    receive(feed.oneOwner);
    expect(lanes.resolve(0, id).problem).toBeUndefined();
    let reply!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      () =>
        new Promise<Response>((resolve) => {
          reply = resolve;
        }),
    );
    const pending = catalogs.validate(id, () => true);
    const rejected = expect(pending).rejects.toThrow(`There is no catalog ${id}`);
    receive(feed.withdrawn);
    expect(lanes.resolve(0, id)).toEqual({
      catalog: drawing,
      pending: false,
      problem: `There is no catalog ${id}`,
    });
    // A pre-withdrawal HTTP reply cannot restore availability.
    reply(Response.json(feed.initial));
    await rejected;
    expect(lanes.resolve(0, id).problem).toBe(`There is no catalog ${id}`);
    receive(feed.restored);
    expect(lanes.resolve(0, id).problem).toBeUndefined();
    expect(catalogs.compiledRevision(id)).toBe(feed.initial.revision);
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
