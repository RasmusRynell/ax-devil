// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import type { EventItem } from "./eventItem";
import { back, EMPTY, jump, push, showRoot, step, titleOf, topObject } from "./pageStack";

const item = (id: number, label = `Merge ${id}`): EventItem => ({
  id,
  frame: 3,
  timeUs: undefined,
  kind: "merge",
  label,
  objects: [],
});
const object = (entity: string) => ({ kind: "object", entity }) as const;
const event = (id: number) => ({ kind: "event", event: item(id) }) as const;

describe("pages of the media tools", () => {
  it("steps and marks the row of the list a page was opened from, and only a page opened from the list that shows", () => {
    const onEvents = showRoot(EMPTY, "events");
    const opened = push(onEvents, event(4));
    expect(opened.entries[0].origin).toBe("events");
    // An object opened from the event page comes from no list.
    const deeper = push(opened, object("7"));
    expect(deeper.entries.map(({ origin }) => origin)).toEqual(["events", null]);
    expect(topObject(deeper)).toEqual(object("7"));
    expect(topObject(back(deeper))).toBeNull();
    // Back from the first page leaves its row marked; stepping keeps the list it came from.
    expect(back(opened).mark).toEqual(event(4));
    const stepped = step(opened, event(5));
    expect(stepped.entries).toEqual([{ key: event(5), origin: "events" }]);
    expect(stepped.mark).toEqual(event(5));

    // An object picked on the picture while the Events list shows comes from no list, and a second one replaces it.
    const picked = jump(onEvents, object("7"));
    expect(picked.entries).toEqual([{ key: object("7"), origin: null }]);
    expect(jump(picked, object("9")).entries).toEqual([{ key: object("9"), origin: null }]);
    expect(push(EMPTY, object("7")).entries[0].origin).toBe("objects");
  });

  it("closes every page when another list shows, and is titled by an object's id or an event's label", () => {
    const open = push(push(EMPTY, object("7")), event(4));
    expect(showRoot(open, "events")).toEqual({ entries: [], mark: null, root: "events" });
    expect(titleOf(object("7"))).toBe("7");
    expect(titleOf(event(4))).toBe("Merge 4");
    expect(titleOf({ kind: "event", event: item(4, "") })).toBe("merge");
  });
});
