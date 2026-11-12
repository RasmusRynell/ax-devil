// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { after, found, READY, readyLane, unavailable } from "./fakeFileAnswer";
import { basisOf, FileRequests, type Basis, type FileRequestsHost } from "./fileRequests";
import type { ClientMessage } from "../stream/protocol";

const FAILED = { state: "Failed", code: "Internal", params: {}, reason: "Disk full" } as const;
const ask = (lane: number, request: number): ClientMessage => ({
  type: "Event",
  lane,
  request,
  event: 0,
  bins: 0,
});

/** A list of kind Events, whose page is what was asked, and a lookup of kind Event; they share a lane and a socket. */
function rig() {
  const lanes = readyLane();
  const sent: ClientMessage[] = [];
  const released: number[] = [];
  let owner = true;
  let ids = 0;
  const host: FileRequestsHost = {
    lanes,
    send: (message) => sent.push(message),
    after,
    owner: () => owner,
    request: () => ++ids,
    changed: () => {},
    released: (lane) => released.push(lane),
  };
  const list = new FileRequests<"Events", string, Basis, { asked: string; data: number }>(
    {
      kind: "Events",
      shape: "list",
      basis: basisOf,
      message: ask,
      page: (_, asked, data) => ({ asked, data }),
    },
    host,
  );
  const lookup = new FileRequests<"Event", number, Basis, number>(
    {
      kind: "Event",
      shape: "lookup",
      basis: basisOf,
      message: ask,
      page: (body, asked) => (body.event === asked ? body.event : null),
    },
    host,
  );
  const rows = (request: number, over?: Parameters<typeof found>[2]) =>
    found(
      request,
      { kind: "Events", total: 1, matched: 1, offset: 0, rows: [], located: null, frame_row: null },
      over,
    );
  const event = (request: number, number: number) =>
    found(request, { kind: "Event", event: number, detail: null });
  /** The id of the newest request sent. */
  const last = () => (sent.at(-1) as { request: number }).request;
  const wait = (ms = 150) => vi.advanceTimersByTime(ms);
  const setOwner = (value: boolean) => (owner = value);
  return { lanes, sent, released, list, lookup, rows, event, last, wait, setOwner };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("a list", () => {
  it("sends only the last of quick changes, at once when flushed", () => {
    const t = rig();
    t.list.want(0, "a");
    t.wait(100);
    t.list.want(0, "b");
    t.wait(149);
    expect(t.sent).toEqual([]);
    expect(t.list.view(0)).toEqual({ pending: true, page: null, unavailable: null });
    t.wait(1);
    expect(t.sent).toHaveLength(1);
    t.list.want(0, "c");
    t.list.flush(0);
    t.wait();
    expect(t.sent).toHaveLength(2);
  });

  it("keeps its page while a newer one is awaited, and drops answers that are not the newest's", () => {
    const t = rig();
    t.list.want(0, "a");
    t.wait();
    const first = t.last();
    t.list.want(0, "b");
    // The answer to the request made before the change is no longer wanted.
    t.list.receive(t.rows(first));
    t.wait();
    t.list.receive(t.rows(first));
    expect(t.list.view(0)).toMatchObject({ pending: true, page: null });
    // Nor is one made under other revisions than the lane has.
    t.list.receive(t.rows(t.last(), { data_revision: 2 }));
    t.list.receive(t.rows(t.last(), { policy_revision: 2 }));
    expect(t.list.view(0).page).toBeNull();
    t.list.receive(t.rows(t.last()));
    expect(t.list.view(0)).toEqual({
      pending: false,
      page: { asked: "b", data: 1 },
      unavailable: null,
    });
    t.list.want(0, "c");
    expect(t.list.view(0)).toMatchObject({ pending: true, page: { asked: "b" } });
  });

  it("keeps its page when a refresh fails, until the file's data changes", () => {
    const t = rig();
    t.list.want(0, "a");
    t.wait();
    t.list.receive(t.rows(t.last()));
    t.lanes.set(0, { tolerance_ms: 120 });
    t.list.refresh();
    t.wait();
    t.list.receive(unavailable(t.last(), FAILED));
    expect(t.list.view(0)).toMatchObject({ page: { asked: "a" }, unavailable: FAILED });
    // Retry asks again, and its page ends the failure.
    t.list.flush(0);
    t.list.receive(t.rows(t.last(), { policy_revision: 2 }));
    expect(t.list.view(0)).toMatchObject({ page: { asked: "a" }, unavailable: null });
    t.lanes.setStatus(0, { ...READY, data_revision: 2 });
    t.list.refresh();
    t.wait();
    t.list.receive(unavailable(t.last(), FAILED));
    expect(t.list.view(0)).toMatchObject({ page: null, unavailable: FAILED });
  });

  const changes: [string, (t: ReturnType<typeof rig>) => void][] = [
    ["a new Ready revision", (t) => t.lanes.setStatus(0, { ...READY, data_revision: 2 })],
    ["a new policy revision", (t) => t.lanes.set(0, { tolerance_ms: 120 })],
    ["the ingest ending", (t) => t.lanes.setStatus(0, READY)],
    ["the viewer owning its frames again", (t) => t.setOwner(true)],
  ];

  it.each(changes)("asks again on %s, not on progress of the same file", (_, change) => {
    const t = rig();
    t.lanes.setStatus(0, { ...READY, ingest: { state: "Reading" } });
    t.setOwner(false);
    t.list.want(0, "a");
    t.wait();
    t.list.receive(unavailable(t.last(), { state: "Reading" }));
    t.lanes.setStatus(0, { ...READY, ingest: { state: "Reading" }, samples: 2000 });
    t.list.refresh();
    t.wait(1000);
    expect(t.sent).toHaveLength(1);
    change(t);
    t.list.refresh();
    t.wait();
    expect(t.sent).toHaveLength(2);
    expect(t.list.view(0).pending).toBe(true);
  });

  it("asks again once the socket is renewed, and stops when released, telling the host once and only if asked", () => {
    const t = rig();
    t.list.want(0, "a");
    t.list.release(0);
    t.wait(5000);
    expect([t.sent, t.released]).toEqual([[], []]);

    t.list.want(0, "a");
    t.wait();
    t.list.suspend();
    t.list.refresh();
    t.wait();
    expect(t.sent).toHaveLength(2);
    t.list.release(0);
    t.list.release(0);
    expect(t.released).toEqual([0]);

    t.list.want(0, "a");
    t.wait();
    t.list.suspend();
    t.list.release(0);
    t.list.refresh();
    t.wait(5000);
    expect(t.released).toEqual([0]);
    expect(t.sent).toHaveLength(3);
  });

  it.each([
    ["is not asked again by a follower", false, 1],
    ["is asked again three times by an owner, as the backend may not know it yet", true, 4],
  ])("%s", (_, owner, asks) => {
    const t = rig();
    t.setOwner(owner);
    t.list.want(0, "a");
    t.wait();
    for (let tries = 0; tries < 6; tries++) {
      t.list.receive(unavailable(t.last(), { state: "Following" }));
      t.wait(1000);
    }
    expect(t.sent).toHaveLength(asks);
    expect(t.list.view(0)).toMatchObject({ pending: false, unavailable: { state: "Following" } });
  });
});

describe("a lookup", () => {
  it("asks at once and shows nothing until answered, nor the old answer when it asks again", () => {
    const t = rig();
    t.lookup.want(0, 4);
    expect(t.sent).toHaveLength(1);
    expect(t.lookup.view(0)).toEqual({ pending: true, page: null, unavailable: null });
    t.lookup.receive(t.event(t.last(), 4));
    expect(t.lookup.view(0)).toEqual({ pending: false, page: 4, unavailable: null });
    t.lanes.setStatus(0, { ...READY, data_revision: 2 });
    t.lookup.refresh();
    expect(t.sent).toHaveLength(2);
    expect(t.lookup.view(0)).toMatchObject({ pending: true, page: null });
  });

  it("drops an answer about something else than was asked", () => {
    const t = rig();
    t.lookup.want(0, 4);
    t.lookup.receive(t.event(t.last(), 5));
    expect(t.lookup.view(0).page).toBeNull();
    t.lookup.receive(t.event(t.last(), 4));
    expect(t.lookup.view(0).page).toBe(4);
  });
});
