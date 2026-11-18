import type { ClientMessage } from "../stream/protocol";
import type { EventRowWire } from "../generated/api";
import { FileScope } from "../inspect/fileScope";
import { after, found, READY, readyLane } from "../inspect/fakeFileAnswer";
import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vite-plus/test";
import { itemOf, type EventItem } from "./eventItem";
import { EMPTY, back, jump, push, showRoot } from "./pageStack";
import { useMediaTools } from "./useMediaTools";

const event: EventItem = {
  id: 4,
  frame: 3,
  generation: 0,
  timeUs: undefined,
  kind: "merge",
  label: "Merge 1 → 2",
  objects: [],
};

it("keeps event pages and list marks attached to their lane and anchored identity across replacement", () => {
  const select = vi.fn();
  const session = { select };
  const lanes = readyLane();
  const sent: ClientMessage[] = [];
  const files = new FileScope(lanes, (message) => sent.push(message), after);
  const source = { files, frameTime: (frame: number) => frame * 40_000 };
  let visible = false;
  const { result, rerender } = renderHook(() => useMediaTools(session, true, visible, source));
  const on = (lane: number) => ({ lane, source: lane });

  // An event page on lane 0, then an object picked on lane 1's picture.
  act(() => result.current.navigate(push(EMPTY, { kind: "event", event }), on(0)));
  act(() =>
    result.current.navigate(jump(result.current.pages, { kind: "object", entity: "9" }), on(1)),
  );
  expect(result.current.pages.entries.map(({ key }) => key.kind)).toEqual(["object"]);
  expect(select).toHaveBeenLastCalledWith({ lane: 1, source: 1, entity: "9" });
  act(() => result.current.navigate(back(result.current.pages), on(1)));
  expect(result.current.pages.entries).toEqual([]);

  // On the same lane the history stays.
  act(() => result.current.navigate(push(EMPTY, { kind: "event", event }), on(1)));
  act(() =>
    result.current.navigate(push(result.current.pages, { kind: "object", entity: "9" }), on(1)),
  );
  expect(result.current.pages.entries.map(({ key }) => key.kind)).toEqual(["event", "object"]);

  // Removing a missing event reveals and selects the underlying object even while the panel is closed.
  act(() => result.current.navigate(push(EMPTY, { kind: "object", entity: "9" }), on(0)));
  const anchored = { ...event, anchor: Array(32).fill(0) as EventRowWire["anchor"], revision: 1 };
  act(() =>
    result.current.navigate(push(result.current.pages, { kind: "event", event: anchored }), on(0)),
  );
  act(() => {
    lanes.setStatus(0, { ...READY, data_revision: 2 });
    files.refresh();
  });
  const ask = sent.filter((message) => message.type === "FindEvent").at(-1);
  if (ask?.type !== "FindEvent") throw new Error("the stack must remap its event");
  act(() =>
    files.receive(
      found(ask.request, { kind: "Event", event: event.id, detail: null }, { data_revision: 2 }),
    ),
  );
  expect(result.current.pages.entries.map(({ key }) => key.kind)).toEqual(["object"]);
  expect(select).toHaveBeenLastCalledWith({ lane: 0, source: 0, entity: "9" });

  // Normal UI: mark B in Events, Back, pick an object, then open A from its object page and C from A’s Same moment list.
  const row = (id: number, frame: number, object: string): EventRowWire => ({
    event: id,
    frame,
    kind: "delete",
    label: `Delete ${object}`,
    objects: [{ id: object, class: null }],
    anchor: Array(32).fill(Number(object)) as EventRowWire["anchor"],
  });
  const b = row(0, 10, "1");
  const a = row(1, 20, "2");
  const c = row(2, 20, "3");
  visible = true;
  rerender();
  act(() =>
    result.current.navigate(
      push(showRoot(EMPTY, "events"), { kind: "event", event: itemOf(b, source.frameTime, 2) }),
      on(0),
    ),
  );
  act(() => result.current.navigate(back(result.current.pages), on(0)));
  act(() => result.current.pick({ lane: 0, source: 0, entity: "2" }));
  for (const event of [a, c])
    act(() =>
      result.current.navigate(
        push(result.current.pages, { kind: "event", event: itemOf(event, source.frameTime, 2) }),
        on(0),
      ),
    );
  act(() => {
    lanes.setStatus(0, { ...READY, data_revision: 3 });
    files.refresh();
  });
  const answer = (row: EventRowWire) => {
    const ask = sent
      .filter((message) => message.type === "FindEvent" || message.type === "Event")
      .at(-1);
    if (!ask || (ask.type !== "FindEvent" && ask.type !== "Event"))
      throw new Error("event request expected");
    act(() =>
      files.receive(
        found(
          ask.request,
          {
            kind: "Event",
            event: ask.event,
            detail: { row, objects: [], same_moment: [], same_moment_truncated: false },
          },
          { data_revision: 3 },
        ),
      ),
    );
  };
  // The producer's new order is C, B, A: old indices are reused by different events.
  const nextA = { ...a, event: 2, frame: 12 };
  const nextC = { ...c, event: 0, frame: 4 };
  const nextB = { ...b, event: 1, frame: 8 };
  answer(nextA);
  answer(nextC);
  answer(nextB);
  const eventOnTop = () => {
    const key = result.current.pages.entries.at(-1)?.key;
    if (key?.kind !== "event") throw new Error("event page expected");
    return key.event;
  };
  expect(eventOnTop()).toMatchObject({ id: 0, frame: 4, label: c.label, revision: 3 });
  answer(nextC);
  act(() => result.current.navigate(back(result.current.pages), on(0)));
  expect(eventOnTop()).toMatchObject({ id: 2, frame: 12, label: a.label, revision: 3 });
  answer(nextA);
  act(() => result.current.navigate(back(result.current.pages), on(0)));
  act(() => result.current.navigate(back(result.current.pages), on(0)));
  expect(result.current.pages.mark).toMatchObject({
    kind: "event",
    event: { id: 1, frame: 8, label: b.label, revision: 3 },
  });
});
