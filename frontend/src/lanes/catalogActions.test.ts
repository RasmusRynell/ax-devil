// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import feed from "../../../fixtures/catalog-export/http-feed.json";
import { choicesOf } from "../export/lanes";
import { catalogDocument } from "../stream/fakeData";
import { setup } from "../playback/fakePlayback";
import { READY } from "../inspect/fakeFileAnswer";
import { laneNotices } from "./laneNotices";
import type { ClientMessage, LaneInfo } from "../stream/protocol";

afterEach(() => vi.unstubAllGlobals());
const TWO_LANES: LaneInfo[] = [
  { label: "DPM", slot: "DPM", source: 0 },
  { label: "GT", slot: "GT", source: 1 },
];
const policies = (sent: ClientMessage[]) => sent.filter((message) => message.type === "LanePolicy");

describe("catalog actions", () => {
  it("gives the other lanes a lane's settings but its visibility, as one policy each", async () => {
    const { stream, server, sent } = await setup();
    server.open(10, TWO_LANES);
    server.catalog();
    stream.setLane(1, { visible: false });
    stream.setLane(0, { sticky: { enabled: true, timeout_ms: 500, opacity: 0.5 }, theme: "dusk" });
    const before = policies(sent).length;
    vi.stubGlobal("window", { sessionStorage: { getItem: () => null } });
    const unchanged = stream.laneSettings();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ revision: 5, document: "{}" })),
    );
    await stream.applyToAll(0);
    expect(stream.laneSettings()).toEqual(unchanged);
    expect(laneNotices(stream.getSnapshot().lanes[0])).toHaveLength(1);
    server.catalog("built-in:standard", catalogDocument(), 6);
    expect(laneNotices(stream.getSnapshot().lanes[0])).toEqual([]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ revision: 7, document: "{}" })),
    );
    await stream.applyToAll(0);
    expect(laneNotices(stream.getSnapshot().lanes[0])).toHaveLength(1);
    stream.setLane(0, { catalog: "built-in:tracking" });
    server.catalog("built-in:tracking", catalogDocument("tracking"));
    expect(laneNotices(stream.getSnapshot().lanes[0])).toEqual([]);
    stream.setLane(0, { catalog: "built-in:standard" });
    server.catalog("built-in:standard", catalogDocument(), 8);
    let finish!: (value: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      ),
    );
    const stale = stream.applyToAll(0);
    server.catalog("built-in:standard", "{}", 10);
    finish(Response.json({ revision: 9, document: catalogDocument() }));
    await stale;
    expect(stream.laneSettings()).toEqual(unchanged);
    expect(stream.getSnapshot().lanes[0].catalogProblem).toBeTruthy();
    const pending = stream.applyToAll(0);
    stream.setLane(1, { theme: "newer" });
    finish(Response.json({ revision: 11, document: catalogDocument() }));
    await pending;
    expect(stream.laneSettings()[1].theme).toBe("newer");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ revision: 11, document: catalogDocument() })),
    );
    await stream.applyToAll(0);
    expect(policies(sent).slice(before)).toMatchObject([
      { lane: 1, subscribed: false, policy: { sticky: { enabled: true, timeout_ms: 500 } } },
    ]);
    expect(stream.laneSettings()[1]).toMatchObject({ theme: "dusk", visible: false });
    expect(stream.getSnapshot().lanes[1].settings.sticky.opacity).toBe(0.5);
  });

  it("exports the drawing the real feed retained when an HTTP-validated edit is coalesced away", async () => {
    const { stream, server, sent } = await setup();
    server.open(10, TWO_LANES);
    server.status(0, READY);
    stream.setLane(0, { catalog: feed.initial.id });
    server.catalog(feed.initial.id, feed.initial.document, feed.initial.revision);
    let release!: () => void;
    const delivery = new Promise<void>((resolve) => {
      release = resolve;
    }).then(() => {
      server.catalog(feed.broken.id, feed.broken.document, feed.broken.revision);
    });
    vi.stubGlobal("window", { sessionStorage: { getItem: () => null } });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(feed.http)),
    );
    await stream.applyToAll(0);
    expect(stream.laneSettings()[1].catalog).toBe(feed.initial.id);
    release();
    await delivery;
    expect(laneNotices(stream.getSnapshot().lanes[0])).toHaveLength(1);
    const wire = choicesOf(stream.getSnapshot().lanes)[0].wire!;
    // These acceptance results came from Exports::begin after the actual Library/CatalogFeed/HTTP sequence.
    expect(feed.exportable).toContain(wire.catalog.revision);
    expect(wire.catalog.revision).toBe(feed.initial.revision);
    expect(feed.exportable).not.toContain(feed.http.revision);
    stream.exports.start({
      lanes: [wire],
      first: 0,
      last: 0,
      format: { format: "Png" },
      path: "out.png",
    });
    expect(sent.at(-1)).toMatchObject({ type: "Export", lanes: [{ catalog: wire.catalog }] });
  });

  it("expires Apply refusals on recovery, selection, dismissal and lane replacement without clearing backend errors", async () => {
    const { stream, server } = await setup();
    server.open(10, TWO_LANES);
    server.catalog();
    server.say({ type: "LaneProblem", lane: 0, reason: "Backend error" });
    vi.stubGlobal("window", { sessionStorage: { getItem: () => null } });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { code: "CatalogInvalid", params: {}, reason: "Reload refused" },
          { status: 400 },
        ),
      ),
    );
    const refusal = () => stream.getSnapshot().lanes[0].catalogActionProblem;
    const backend = () => expect(stream.getSnapshot().lanes[0].problem).toBe("Backend error");
    await stream.applyToAll(0);
    expect(laneNotices(stream.getSnapshot().lanes[0])).toEqual([
      { problem: "Backend error" },
      { problem: "Could not apply the catalog to all lanes", reasons: ["Reload refused"] },
    ]);
    stream.dismissCatalogAction(0);
    expect(refusal()).toBeUndefined();
    backend();
    await stream.applyToAll(0);
    server.catalog("built-in:standard", catalogDocument(), 2);
    expect(refusal()).toBeUndefined();
    backend();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ revision: 3, document: catalogDocument() })),
    );
    await stream.applyToAll(0);
    backend();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { code: "CatalogInvalid", params: {}, reason: "Reload refused" },
          { status: 400 },
        ),
      ),
    );
    await stream.applyToAll(0);
    stream.setLane(0, { catalog: "built-in:tracking" });
    expect(refusal()).toBeUndefined();
    backend();
    server.catalog("built-in:tracking", catalogDocument("tracking"));
    await stream.applyToAll(0);
    server.open(10, [{ label: "New lane", slot: "New lane", source: 2 }]);
    expect(refusal()).toBeUndefined();
  });
});
