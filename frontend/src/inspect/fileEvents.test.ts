// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import type { EventDetailWire, EventRowWire } from "../generated/api";
import { after, found, readyLane, unavailable } from "./fakeFileAnswer";
import { FileScope } from "./fileScope";
import type { ClientMessage } from "../stream/protocol";

const ROW: EventRowWire = {
  anchor: Array(32).fill(0) as EventRowWire["anchor"],
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

/** A file scope over one Ready lane, a recording socket and fake timers. */
function scope() {
  const sent: ClientMessage[] = [];
  const files = new FileScope(readyLane(), (message) => sent.push(message), after);
  const queries = () =>
    sent.filter(
      (message): message is Extract<ClientMessage, { type: "EventQuery" }> =>
        message.type === "EventQuery",
    );
  const rows = (
    request: number,
    over: { located?: number | null; frame_row?: number | null } = {},
  ) =>
    found(request, {
      kind: "Events",
      total: 30,
      matched: 1,
      offset: 0,
      rows: [ROW],
      located: null,
      frame_row: null,
      ...over,
    });
  const last = () => queries().at(-1)?.request ?? 0;
  return { files, events: files.events, sent, queries, rows, last };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("the Events list", () => {
  it("sends only the last of quick changes, parsed, and nothing for what is already wanted", () => {
    const { events, queries } = scope();
    events.request(0, "m", 0, 200, null, null);
    vi.advanceTimersByTime(100);
    events.request(0, "kind:merge object:7", 50, 200, 4, null);
    vi.advanceTimersByTime(150);
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
    events.request(0, "", 0, 200, 4, 300);
    vi.advanceTimersByTime(150);
    // The answer takes 400 ms; the frame moves every 40 ms meanwhile.
    for (let frame = 301; frame < 311; frame++) {
      events.request(0, "", 0, 200, 4, frame);
      vi.advanceTimersByTime(40);
    }
    expect(queries()).toHaveLength(1);
    files.receive(rows(last(), { located: 7, frame_row: 50 }));
    // The page keeps the frame it was asked about and the event it located.
    expect(events.view(0)).toMatchObject({
      pending: false,
      page: { total: 30, located: { event: 4, row: 7 }, frame: 300, frameRow: 50 },
    });
    vi.advanceTimersByTime(250);
    expect(queries().map(({ frame }) => frame)).toEqual([300, 310]);
  });
});

describe("one event", () => {
  it("asks for the event at once, and an event the file does not have is an answer too", () => {
    const { events, files, sent } = scope();
    events.want(0, 4, 64);
    expect(sent).toEqual([{ type: "Event", lane: 0, request: 1, event: 4, bins: 64 }]);
    expect(events.event(0)).toBeNull();
    files.receive(found(1, { kind: "Event", event: 4, detail: DETAIL }));
    expect(events.event(0)).toMatchObject({ event: 4, detail: { same_moment_truncated: true } });
    events.want(0, 5, 64);
    files.receive(found(2, { kind: "Event", event: 5, detail: null }));
    expect(events.event(0)).toMatchObject({ event: 5, detail: null });
    files.suspend();
    sent.length = 0;
    events.want(0, null);
    expect(sent).toEqual([]);
  });

  it("says why there is no event, to whoever listens, until another is wanted", () => {
    const { events, files } = scope();
    const heard = vi.fn();
    files.subscribe(heard);
    events.want(0, 4, 0);
    heard.mockClear();
    files.receive(unavailable(1, { state: "TooLarge" }));
    expect(events.eventUnavailable(0)).toEqual({ state: "TooLarge" });
    expect(heard).toHaveBeenCalled();
    events.want(0, 5, 0);
    expect(events.eventUnavailable(0)).toBeNull();
  });
});
