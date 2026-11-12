// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import type { FileBody, FileRowWire, Located } from "../generated/api";
import { after, found, READY, readyLane } from "../inspect/fakeFileAnswer";
import { FileScope } from "../inspect/fileScope";
import { setup } from "./fakePlayback";
import type { ClientMessage } from "../stream/protocol";

const SORT = { key: "id", descending: false } as const;
const ROW: FileRowWire = {
  id: "7",
  classes: ["Car"],
  events: [],
  first_frame: 3,
  last_frame: 9,
  presence: [[3, 9]],
};
const objects = (located: Located | null = null): FileBody => ({
  kind: "Objects",
  total: 40,
  matched: 1,
  offset: 0,
  rows: [ROW],
  located,
});

/** A file scope over one Ready lane, a recording socket and fake timers. */
function scope() {
  const lanes = readyLane();
  const sent: ClientMessage[] = [];
  const files = new FileScope(lanes, (message) => sent.push(message), after);
  const queries = () => sent.filter((message) => message.type === "FileQuery");
  const ask = (text = "car") => {
    files.request(0, text, SORT, 0, 50, 64);
    vi.advanceTimersByTime(150);
  };
  /** The id of the newest request sent. */
  const last = () => (queries().at(-1) as Extract<ClientMessage, { type: "FileQuery" }>).request;
  return { lanes, files, sent, queries, ask, last };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("the objects list", () => {
  it("sends only the last of quick changes, parsed, with the lane's class toggles", () => {
    const { files, queries } = scope();
    files.request(0, "c", SORT, 0, 50, 64);
    vi.advanceTimersByTime(100);
    files.request(0, "class:car OR id>3", { key: "class", descending: true }, 50, 50, 64);
    vi.advanceTimersByTime(150);
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
        locate: null,
      },
    ]);
  });

  it("carries the object the cursor moved to in the next question, and asks nothing for the move", () => {
    const { files, lanes, queries, ask, last } = scope();
    const locate = () => (queries().at(-1) as { locate: string | null }).locate;
    ask();
    files.receive(found(last(), objects()));
    files.locating(0, "7");
    vi.advanceTimersByTime(1000);
    expect(queries()).toHaveLength(1);
    // A refresh the lane's class toggles cause asks about the object the cursor is on now.
    lanes.set(0, { filter: { show_bus: true } });
    files.refresh();
    vi.advanceTimersByTime(150);
    expect(locate()).toBe("7");

    // A move while a question is out: an answer that holds neither that object nor its row is followed by a new question.
    files.locating(0, "99");
    files.receive(found(last(), objects({ id: "7", row: 3 })));
    expect(files.view(0).pending).toBe(true);
    vi.advanceTimersByTime(150);
    expect(locate()).toBe("99");
    files.receive(found(last(), objects({ id: "99", row: null })));
    vi.advanceTimersByTime(1000);
    expect(queries()).toHaveLength(3);
  });
});

describe("a lane the list, an event and an object's appearances share", () => {
  it("is freed on the backend only once none of them wants it", () => {
    const t = scope();
    const { appearances, events } = t.files;
    const releases = () => t.sent.filter((message) => message.type === "FileRelease").length;
    // An object page in Frame scope: the list is not wanted.
    appearances.want(0, "7");
    appearances.want(0, null);
    expect(releases()).toBe(1);

    // The list in File scope still wants the lane when the page closes, and the page when the list goes.
    t.ask();
    appearances.want(0, "7");
    appearances.want(0, null);
    appearances.want(0, "7");
    t.files.release(0);
    expect(releases()).toBe(1);
    // Moving to another event keeps the lane.
    events.want(0, 4);
    events.want(0, 6);
    appearances.want(0, null);
    expect(releases()).toBe(1);
    events.want(0, null);
    expect(releases()).toBe(2);
  });
});

describe("in a playback session", () => {
  it("asks once the lane is ready and again when the viewer follows, then owns its frames", async () => {
    vi.useRealTimers();
    const { stream, server, sent, retries } = await setup();
    server.open(10, [{ label: "DPM", slot: "DPM", source: 0 }]);
    server.status(0, READY);
    stream.files.request(0, "car", SORT, 0, 50, 64);
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
    server.say(found(request, objects(), { policy_revision: policy }));
    expect(stream.files.view(0).page?.rows).toEqual([ROW]);
    stream.follow({ frame: 0, playing: false, speedCenti: 100, atUs: 0 }, () => 0);
    debounce();
    expect(queries()).toHaveLength(2);
  });
});
