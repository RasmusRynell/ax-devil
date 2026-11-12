import type { ClientMessage } from "../stream/protocol";
import type { EventRowWire } from "../generated/api";
import { FileScope } from "../inspect/fileScope";
import { after, found, READY, readyLane } from "../inspect/fakeFileAnswer";
import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vite-plus/test";
import type { EventItem } from "./eventItem";
import { EMPTY, back, jump, push } from "./pageStack";
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

it("leaves the pages of a lane behind when a page opens on another lane, so Back never reads one lane's event from another's file", () => {
  const select = vi.fn();
  const session = { select };
  const lanes = readyLane();
  const sent: ClientMessage[] = [];
  const files = new FileScope(lanes, (message) => sent.push(message), after);
  const source = { files, frameTime: () => 0 };
  const { result } = renderHook(() => useMediaTools(session, true, false, source));
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
});
