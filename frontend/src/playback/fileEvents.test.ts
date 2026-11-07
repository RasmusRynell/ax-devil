// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import type { EventDetailWire, EventRowWire } from "../generated/api";
import { LANE_SETTINGS } from "./fakeOpened";
import { FileScope } from "./fileScope";
import { LaneSet } from "./lanes";
import type { ClientMessage, LaneStatusWire, ServerMessage } from "./protocol";

const READY: Extract<LaneStatusWire, { status: "Ready" }> = {
  status: "Ready",
  bases: ["Frame"],
  default_basis: "Frame",
  filter: [],
  warnings: [],
  samples: 1000,
  data_revision: 1,
  alignment: null,
  mean_sample_bytes: 100,
  ingest: { state: "Complete" },
};
const ROW: EventRowWire = {
  event: 4,
  frame: 12,
  kind: "merge",
  label: "Merge 1 → 2",
  objects: [{ id: "1", class: "car" }],
};
const DETAIL: EventDetailWire = {
  row: ROW,
  objects: [{ id: "1", classes: ["car"], seen: [3, 20], presence: [[3, 20]] }],
  same_moment: [],
  same_moment_truncated: true,
};

type Rows = Extract<ServerMessage, { type: "EventRows" }>;

/** A file scope over one Ready lane, a recording socket and fake timers. */
function scope() {
  const lanes = new LaneSet();
  lanes.open([{ label: "DPM", slot: "DPM", source: 0 }], LANE_SETTINGS);
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
  const events = files.events;
  const queries = () =>
    sent.filter(
      (message): message is Extract<ClientMessage, { type: "EventQuery" }> =>
        message.type === "EventQuery",
    );
  const rows = (request: number, over: Partial<Extract<Rows["rows"], { state: "Found" }>> = {}) =>
    ({
      type: "EventRows",
      lane: 0,
      request,
      rows: {
        state: "Found",
        data_revision: 1,
        policy_revision: 1,
        total: 30,
        matched: 1,
        offset: 0,
        rows: [ROW],
        located: null,
        frame_row: null,
        ...over,
      },
    }) satisfies Rows;
  const last = () => queries().at(-1)?.request ?? 0;
  return { lanes, files, events, sent, queries, rows, last };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("the Events list", () => {
  it("sends only the last of quick changes, parsed, and nothing for what is already wanted", () => {
    const { events, queries } = scope();
    events.request(0, "m", 0, 200, null, null);
    vi.advanceTimersByTime(100);
    events.request(0, "kind:merge object:7", 50, 200, 4, null);
    vi.advanceTimersByTime(149);
    expect(queries()).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(queries()).toEqual([
      {
        type: "EventQuery",
        lane: 0,
        request: 1,
        query: {
          groups: [
            [{ path: "kind", op: "eq", value: "merge", negated: false }],
            [{ path: "object", op: "eq", value: "7", negated: false }],
          ],
        },
        offset: 50,
        limit: 200,
        locate: 4,
        frame: null,
      },
    ]);
    events.request(0, "kind:merge object:7", 50, 200, 4, null);
    vi.advanceTimersByTime(1000);
    expect(queries()).toHaveLength(1);
  });

  it("asks where a frame is at most every quarter second however often the frame moves", () => {
    const { events, files, rows, queries, last } = scope();
    events.request(0, "", 0, 200, null, null);
    vi.advanceTimersByTime(150);
    files.receive(rows(last()));
    // A frame the rows do not reach, moving as the video plays.
    for (let frame = 300; frame < 310; frame++) {
      events.request(0, "", 0, 200, null, frame);
      vi.advanceTimersByTime(40);
    }
    expect(queries().map(({ frame }) => frame)).toEqual([null, 306]);
    files.receive(rows(last()));
    vi.advanceTimersByTime(250);
    expect(queries().map(({ frame }) => frame)).toEqual([null, 306, 309]);
  });

  it("sends no new question about the frame while one is out, so a slow answer is never stale", () => {
    const { events, files, rows, queries, last } = scope();
    events.request(0, "", 0, 200, null, 300);
    vi.advanceTimersByTime(150);
    // The answer takes 400 ms; the frame moves every 40 ms meanwhile.
    for (let frame = 301; frame < 311; frame++) {
      events.request(0, "", 0, 200, null, frame);
      vi.advanceTimersByTime(40);
    }
    expect(queries()).toHaveLength(1);
    files.receive(rows(last(), { frame_row: 50 }));
    expect(events.view(0).page).toMatchObject({ frame: 300, frameRow: 50 });
    // The answer asks again, about the frame now.
    vi.advanceTimersByTime(250);
    expect(queries().map(({ frame }) => frame)).toEqual([300, 310]);
  });

  it("keeps the page with the frame it was asked about, and the event it located", () => {
    const { events, files, rows, last } = scope();
    events.request(0, "", 0, 200, 4, 12);
    vi.advanceTimersByTime(150);
    const asked = last();
    // The frame moves on while the question is out.
    events.request(0, "", 0, 200, 4, 13);
    files.receive(rows(asked, { located: 7, frame_row: 3 }));
    expect(events.view(0).page).toMatchObject({
      total: 30,
      matched: 1,
      located: { event: 4, row: 7 },
      frame: 12,
      frameRow: 3,
    });
  });

  it("drops answers of an older request or of other revisions", () => {
    const { events, files, rows, last, lanes } = scope();
    events.request(0, "", 0, 200, null, null);
    vi.advanceTimersByTime(150);
    const first = last();
    events.request(0, "x", 0, 200, null, null);
    vi.advanceTimersByTime(150);
    files.receive(rows(first));
    expect(events.view(0).page).toBeNull();
    files.receive(rows(last(), { data_revision: 2 }));
    expect(events.view(0).page).toBeNull();
    files.receive(rows(last(), { policy_revision: 2 }));
    expect(events.view(0).page).toBeNull();
    files.receive(rows(last()));
    expect(events.view(0)).toMatchObject({ pending: false, page: { revision: 1 } });

    // A new source revision asks again, and the rows of the old one are not kept through a failure.
    lanes.setStatus(0, { ...READY, data_revision: 2 });
    files.refresh();
    expect(events.view(0).pending).toBe(true);
    vi.advanceTimersByTime(150);
    files.receive({
      type: "EventRows",
      lane: 0,
      request: last(),
      rows: { state: "Unavailable", unavailable: { state: "Reading" } },
    });
    expect(events.view(0)).toMatchObject({ page: null, unavailable: { state: "Reading" } });
  });

  it("asks again, three times, while a follower may still become the owner", () => {
    const { events, files, queries, last } = scope();
    events.request(0, "", 0, 200, null, null);
    vi.advanceTimersByTime(150);
    for (let attempt = 1; attempt <= 4; attempt++) {
      files.receive({
        type: "EventRows",
        lane: 0,
        request: last(),
        rows: { state: "Unavailable", unavailable: { state: "Following" } },
      });
      vi.advanceTimersByTime(1000);
      expect(queries()).toHaveLength(Math.min(attempt, 3) + 1);
    }
  });
});

describe("one event", () => {
  const answer = (
    files: FileScope,
    request: number,
    detail: Extract<
      Extract<ServerMessage, { type: "Event" }>["detail"],
      { state: "Found" }
    >["detail"],
  ) =>
    files.receive({
      type: "Event",
      lane: 0,
      request,
      event: 4,
      detail: { state: "Found", data_revision: 1, policy_revision: 1, detail },
    });

  it("asks for the event at once, keeps what the backend says, and truncation with it", () => {
    const { events, files, sent } = scope();
    events.want(0, 4, 64);
    expect(sent).toEqual([{ type: "Event", lane: 0, request: 1, event: 4, bins: 64 }]);
    expect(events.event(0)).toBeNull();
    answer(files, 1, DETAIL);
    expect(events.event(0)).toMatchObject({ event: 4, detail: { same_moment_truncated: true } });
    // An event the file does not have is an answer too.
    events.want(0, 5, 64);
    files.receive({
      type: "Event",
      lane: 0,
      request: 2,
      event: 5,
      detail: { state: "Found", data_revision: 1, policy_revision: 1, detail: null },
    });
    expect(events.event(0)).toMatchObject({ event: 5, detail: null });
  });

  it("says why there is no event, to whoever listens", () => {
    const { events, files } = scope();
    const heard = vi.fn();
    files.subscribe(heard);
    events.want(0, 4, 0);
    heard.mockClear();
    files.receive({
      type: "Event",
      lane: 0,
      request: 1,
      event: 4,
      detail: { state: "Unavailable", unavailable: { state: "TooLarge" } },
    });
    expect(events.eventUnavailable(0)).toEqual({ state: "TooLarge" });
    expect(heard).toHaveBeenCalled();
    // Another event starts with no reason.
    events.want(0, 5, 0);
    expect(events.eventUnavailable(0)).toBeNull();
  });

  it("frees the lane only when no list, event or object of it is wanted", () => {
    const { events, files, sent } = scope();
    const releases = () => sent.filter((message) => message.type === "FileRelease");
    events.request(0, "", 0, 200, null, null);
    vi.advanceTimersByTime(150);
    events.want(0, 4, 0);
    // Moving to another event keeps the lane.
    events.want(0, 6, 0);
    events.want(0, null);
    expect(releases()).toEqual([]);
    files.appearances.want(0, "7");
    events.release(0);
    expect(releases()).toEqual([]);
    files.appearances.want(0, null);
    expect(releases()).toEqual([{ type: "FileRelease", lane: 0 }]);
    events.want(0, 4, 0);
    events.want(0, null);
    expect(releases()).toHaveLength(2);
  });
});
