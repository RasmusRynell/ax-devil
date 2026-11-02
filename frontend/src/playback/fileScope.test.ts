// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import type { FileRowWire } from "../generated/api";
import { FileScope } from "./fileScope";
import { LANE_DEFAULTS } from "./fakeOpened";
import { setup } from "./fakePlayback";
import { LaneSet } from "./lanes";
import type { ClientMessage, LaneStatusWire, ServerMessage } from "./protocol";

const READY: Extract<LaneStatusWire, { status: "Ready" }> = {
  status: "Ready",
  clock: "frame",
  filter: [
    { id: "show_car", label: "Car", default_enabled: true },
    { id: "show_bus", label: "Bus", default_enabled: false },
  ],
  warnings: [],
  samples: 1000,
  data_revision: 1,
  mean_sample_bytes: 100,
  ingest: { state: "Complete" },
};
const SORT = { key: "id", descending: false } as const;
const ROW: FileRowWire = {
  id: "7",
  classes: ["Car"],
  events: [],
  first_frame: 3,
  last_frame: 9,
  presence: [[3, 9]],
};

type Rows = Extract<ServerMessage, { type: "FileRows" }>;
type Scope = Extract<ServerMessage, { type: "FileScope" }>;

/** A file scope over one Ready lane, a recording socket and fake timers. */
function scope() {
  const lanes = new LaneSet();
  lanes.open([{ label: "DPM", source: 0 }], LANE_DEFAULTS);
  lanes.setStatus(0, READY);
  const sent: ClientMessage[] = [];
  const files = new FileScope(
    lanes,
    (message) => sent.push(message),
    (run, ms) => {
      const timer = setTimeout(run, ms);
      return () => clearTimeout(timer);
    },
  );
  const queries = () => sent.filter((message) => message.type === "FileQuery");
  /** The rows the backend would answer request `request` with, under the lane's current revisions unless told. */
  const rows = (request: number, over: Partial<Rows> = {}): Rows => ({
    type: "FileRows",
    lane: 0,
    request,
    data_revision: 1,
    policy_revision: 1,
    total: 40,
    matched: 1,
    offset: 0,
    rows: [ROW],
    ...over,
  });
  const ask = (text = "car") => {
    files.request(0, text, SORT, 0, 50, 64);
    vi.advanceTimersByTime(150);
  };
  /** The id of the newest request sent. */
  const last = () => (queries().at(-1) as Extract<ClientMessage, { type: "FileQuery" }>).request;
  return { lanes, files, sent, queries, rows, ask, last };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("asking", () => {
  it("sends only the last of quick changes, parsed, with the lane's class toggles", () => {
    const { files, queries } = scope();
    files.request(0, "c", SORT, 0, 50, 64);
    vi.advanceTimersByTime(100);
    files.request(0, "class:car OR id>3", { key: "class", descending: true }, 50, 50, 64);
    vi.advanceTimersByTime(149);
    expect(queries()).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(queries()).toEqual([
      {
        type: "FileQuery",
        lane: 0,
        request: 1,
        query: {
          groups: [
            [
              { path: "class", op: "eq", value: "car", negated: false },
              { path: "id", op: "gt", value: "3", negated: false },
            ],
          ],
        },
        // Only the Car option is on by default.
        classes: [0b01],
        sort: { key: "class", descending: true },
        offset: 50,
        limit: 50,
        bins: 64,
      },
    ]);
    expect(files.view(0)).toEqual({ pending: true, page: null, unavailable: null });
  });

  it("keeps showing the last rows while a newer list is awaited, and drops answers of older requests", () => {
    const { files, rows, ask, last } = scope();
    ask("car");
    const first = last();
    files.request(0, "bus", SORT, 0, 50, 64);
    // The answer to the request made before the user typed more is no longer wanted.
    files.receive(rows(first));
    expect(files.view(0).page).toBeNull();
    vi.advanceTimersByTime(150);
    expect(last()).toBeGreaterThan(first);
    files.receive(rows(first));
    expect(files.view(0).pending).toBe(true);
    files.receive(rows(last()));
    expect(files.view(0)).toEqual({
      pending: false,
      page: { total: 40, matched: 1, offset: 0, rows: [ROW] },
      unavailable: null,
    });
    files.request(0, "bus", SORT, 0, 50, 64);
    expect(files.view(0)).toMatchObject({ pending: true, page: { matched: 1 } });
  });

  it("drops rows made under other revisions than the lane has", () => {
    const { files, lanes, rows, ask, last } = scope();
    ask();
    files.receive(rows(last(), { data_revision: 2 }));
    files.receive(rows(last(), { policy_revision: 2 }));
    expect(files.view(0).page).toBeNull();
    lanes.set(0, { tolerance_ms: 120 });
    files.receive(rows(last()));
    expect(files.view(0).page).toBeNull();
  });
});

describe("asking again", () => {
  const changes: [string, (t: ReturnType<typeof scope>) => void][] = [
    ["a new Ready revision", (t) => t.lanes.setStatus(0, { ...READY, data_revision: 2 })],
    ["a new policy revision", (t) => t.lanes.set(0, { tolerance_ms: 120 })],
    ["a class toggle", (t) => t.lanes.set(0, { filter: { show_bus: true } })],
    ["the ingest ending", (t) => t.lanes.setStatus(0, READY)],
    ["the viewer owning its frames again", (t) => t.files.setOwner(true)],
  ];

  it.each(changes)("on %s", (_, change) => {
    const t = scope();
    t.lanes.setStatus(0, { ...READY, ingest: { state: "Reading" } });
    t.files.setOwner(false);
    t.ask();
    t.files.receive({
      type: "FileScope",
      lane: 0,
      request: t.last(),
      unavailable: { state: "Reading" },
    });
    expect(t.files.view(0).unavailable).toEqual({ state: "Reading" });
    // Progress of the same file asks nothing.
    t.lanes.setStatus(0, { ...READY, ingest: { state: "Reading" }, samples: 2000 });
    t.files.refresh();
    vi.advanceTimersByTime(1000);
    expect(t.queries()).toHaveLength(1);
    change(t);
    t.files.refresh();
    vi.advanceTimersByTime(150);
    expect(t.queries()).toHaveLength(2);
    expect(t.files.view(0).pending).toBe(true);
  });

  it("when the socket was renewed, and stops when the list is no longer wanted", () => {
    const t = scope();
    t.ask();
    t.files.suspend();
    t.files.reopen();
    vi.advanceTimersByTime(150);
    expect(t.queries()).toHaveLength(2);
    t.files.refresh();
    t.files.release(0);
    vi.advanceTimersByTime(5000);
    expect(t.queries()).toHaveLength(2);
    expect(t.files.view(0)).toEqual({ pending: false, page: null, unavailable: null });
  });

  it("tells the backend once to free a lane it was asked about, and asks again only for lists still wanted", () => {
    const t = scope();
    const releases = () => t.sent.filter((message) => message.type === "FileRelease");
    t.files.request(0, "car", SORT, 0, 50, 64);
    t.files.release(0);
    vi.advanceTimersByTime(150);
    expect(t.sent).toEqual([]);

    t.ask();
    t.files.release(0);
    t.files.release(0);
    expect(releases()).toEqual([{ type: "FileRelease", lane: 0 }]);

    t.ask();
    t.files.suspend();
    t.files.release(0);
    t.files.reopen();
    vi.advanceTimersByTime(5000);
    expect(releases()).toHaveLength(1);
    expect(t.queries()).toHaveLength(2);
  });
});

describe("a viewer that follows", () => {
  const following: Scope = {
    type: "FileScope",
    lane: 0,
    request: 0,
    unavailable: { state: "Following" },
  };

  it("is told so and is not asked again until it owns its frames", () => {
    const t = scope();
    t.files.setOwner(false);
    t.ask();
    t.files.receive({ ...following, request: t.last() });
    expect(t.files.view(0)).toEqual({
      pending: false,
      page: null,
      unavailable: { state: "Following" },
    });
    vi.advanceTimersByTime(10_000);
    expect(t.queries()).toHaveLength(1);
  });

  it("asks a few more times when the backend still takes an owner for one", () => {
    const t = scope();
    t.ask();
    for (let tries = 0; tries < 6; tries++) {
      t.files.receive({ ...following, request: t.last() });
      vi.advanceTimersByTime(1000);
    }
    expect(t.queries()).toHaveLength(4);
    expect(t.files.view(0)).toMatchObject({ pending: false, unavailable: { state: "Following" } });
  });
});

describe("in a playback session", () => {
  it("asks once the lane is ready and again when the viewer follows, then owns its frames", async () => {
    vi.useRealTimers();
    const { session, server, sent, retries } = await setup();
    server.open(10, [{ label: "DPM", source: 0 }]);
    server.status(0, READY);
    session.files.request(0, "car", SORT, 0, 50, 64);
    const debounce = () => {
      const index = retries.map((timer) => timer.ms).lastIndexOf(150);
      expect(index).toBeGreaterThanOrEqual(0);
      retries.splice(index, 1)[0].run();
    };
    debounce();
    const queries = () => sent.filter((message) => message.type === "FileQuery");
    expect(queries()).toHaveLength(1);
    const request = (queries()[0] as Extract<ClientMessage, { type: "FileQuery" }>).request;
    const policy = sent.filter((message) => message.type === "LanePolicy").length;
    server.say({
      type: "FileRows",
      lane: 0,
      request,
      data_revision: 1,
      policy_revision: policy,
      total: 1,
      matched: 1,
      offset: 0,
      rows: [ROW],
    });
    expect(session.files.view(0).page?.rows).toEqual([ROW]);
    session.follow({ frame: 0, playing: false, speedCenti: 100, atUs: 0 }, () => 0);
    debounce();
    expect(queries()).toHaveLength(2);
  });
});
