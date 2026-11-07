import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import type { EventDetailWire } from "../generated/api";
import { FRAME, fileEvent, mount, says, showEvents } from "./panelRig";

beforeEach(() => vi.stubGlobal("ResizeObserver", undefined));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** The Events list's rows as `kind` and the objects it names. */
const rows = () =>
  within(screen.getByRole("listbox", { name: "Events" }))
    .getAllByRole("option")
    .map((row) => row.textContent);
const page = () => screen.getByRole("region", { name: "Event page" });
const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;
const search = (text: string) => {
  const box = screen.getByRole("textbox", { name: "Search events" });
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
};

const EVENTS = [
  fileEvent(0, 0),
  fileEvent(1, 0, "delete", ["3"]),
  fileEvent(2, 3, "rename", ["7", "8"]),
];

describe("Events list", () => {
  it("lists the file's events in frame order, with the line after those of the frame on screen", () => {
    const { answerEvents, play } = mount();
    showEvents();
    answerEvents(EVENTS);
    expect(rows()).toEqual([
      "00:00:00.000merge1, 2",
      "00:00:00.000delete3",
      "00:00:01.500rename7, 8",
    ]);
    const current = () =>
      screen
        .getAllByRole("option")
        .filter((row) => row.getAttribute("aria-current") === "true")
        .map((row) => row.getAttribute("aria-posinset"));
    // The line lies before row 2 (the third), as the first two are on the frame.
    const line = () => document.querySelector<HTMLElement>("[data-now]")?.style.top;
    expect(current()).toEqual(["1", "2"]);
    expect(line()).toContain("* 2 ");
    // The frames go by: nothing is on frame 1 or 2, the third event is on frame 3.
    play(FRAME);
    expect(current()).toEqual([]);
    expect(line()).toContain("* 2 ");
    play(FRAME);
    play(FRAME);
    expect(current()).toEqual(["3"]);
    expect(line()).toContain("* 3 ");
  });

  it("says how many events there are on its tab, also while the objects show", () => {
    const { answerEvents } = mount();
    showEvents();
    answerEvents(EVENTS);
    expect(screen.getByRole("tab", { name: "Events 3" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: /^Objects/ }));
    expect(screen.getByRole("tab", { name: "Events 3" })).toBeTruthy();
    expect(screen.queryByRole("listbox", { name: "Events" })).toBeNull();
  });

  it("counts the events on its tab before the Events list was ever shown", () => {
    const { answerEvents, eventQueries } = mount();
    expect(screen.getByRole("tab", { name: "Events" })).toBeTruthy();
    act(() => void vi.advanceTimersByTime(250));
    expect(eventQueries().map(({ limit }) => limit)).toEqual([1]);
    answerEvents(EVENTS, { total: 3, matched: 3 });
    expect(screen.getByRole("tab", { name: "Events 3" })).toBeTruthy();
  });

  it("searches by kind and object, says how many it kept and which field no event has", () => {
    const { answerEvents, eventQueries } = mount();
    showEvents();
    answerEvents(EVENTS);
    expect(says("1 of 3")).toBe(false);
    search("kind:merge object:1");
    expect(eventQueries().at(-1)?.query.groups).toEqual([
      [{ path: "kind", op: "eq", value: "merge", negated: false }],
      [{ path: "object", op: "eq", value: "1", negated: false }],
    ]);
    answerEvents([EVENTS[0]], { total: 3, matched: 1 });
    expect(says("1 of 3")).toBe(true);
    expect(rows()).toHaveLength(1);

    search("colour:red");
    answerEvents([], { total: 3, matched: 0 });
    expect(says("No event has colour")).toBe(true);
    expect(screen.getByText("No matching events")).toBeTruthy();
    search("");
    answerEvents([], { total: 0, matched: 0 });
    expect(screen.getByText("No events")).toBeTruthy();
    expect(says("No event has colour")).toBe(false);
  });

  it("keeps its search while the objects show", () => {
    const { answerEvents } = mount();
    showEvents();
    answerEvents(EVENTS);
    search("kind:merge");
    fireEvent.click(screen.getByRole("tab", { name: /^Objects/ }));
    showEvents();
    expect(screen.getByRole("textbox", { name: "Search events" })).toHaveProperty(
      "value",
      "kind:merge",
    );
  });

  it("says why there is nothing to list", () => {
    const { files, eventQueries } = mount();
    showEvents();
    expect(screen.getByText("Loading…")).toBeTruthy();
    act(() => void vi.advanceTimersByTime(250));
    const request = eventQueries().at(-1)?.request ?? 0;
    act(() =>
      files.receive({
        type: "EventRows",
        lane: 0,
        request,
        rows: { state: "Unavailable", unavailable: { state: "PlainLane" } },
      }),
    );
    expect(screen.getByText("This lane has no events")).toBeTruthy();
  });
});

describe("Event pages", () => {
  const detail = (over: Partial<EventDetailWire> = {}): EventDetailWire => ({
    row: EVENTS[0],
    objects: [
      { id: "1", classes: ["car"], seen: [0, 9], presence: [[0, 9]] },
      { id: "2", classes: ["person"], seen: [2, 4], presence: [[2, 4]] },
    ],
    same_moment: [EVENTS[1]],
    same_moment_truncated: false,
    ...over,
  });
  const open = (index: number) => {
    const shown = mount();
    showEvents();
    shown.answerEvents(EVENTS);
    fireEvent.click(screen.getAllByRole("option")[index]);
    return shown;
  };

  it("shows the event, its objects and the other events of its moment, and asks for no more than that", () => {
    const { answerEvent, eventAsks } = open(0);
    expect(eventAsks().at(-1)).toMatchObject({
      event: 0,
      bins: 64,
    });
    expect(within(page()).getByRole("heading").textContent).toBe("Merge 1 → 2");
    expect(within(page()).getByText("1 → 2")).toBeTruthy();
    expect(within(page()).getByText("#0")).toBeTruthy();
    expect(within(page()).getByText("00:00:00.000")).toBeTruthy();
    answerEvent(detail({ same_moment_truncated: true }));
    const objects = within(page()).getByRole("listbox", { name: "Objects involved" });
    expect(
      within(objects)
        .getAllByRole("option")
        .map((row) => row.firstElementChild?.textContent),
    ).toEqual(["1", "2"]);
    // The tick for the event's own frame marks each object's strip.
    expect(page().querySelectorAll("[data-tick='0']")).toHaveLength(2);
    const moment = within(page()).getByRole("region", { name: "Same moment" });
    expect(within(moment).getAllByRole("option")).toHaveLength(1);
    expect(moment.textContent).toContain("more not shown");
  });

  it("goes to the event's frame, to an object and to another event, and back one page at a time", () => {
    const { answerEvent, source, selected } = open(0);
    answerEvent(detail());
    fireEvent.click(button("Go to frame"));
    expect(source.seek).toHaveBeenCalledWith(0);

    fireEvent.click(
      within(page()).getByRole("listbox", { name: "Same moment" }).firstElementChild!
        .firstElementChild!,
    );
    expect(within(page()).getByRole("heading").textContent).toBe("Delete 3");
    answerEvent(detail({ row: EVENTS[1], objects: [], same_moment: [EVENTS[0]] }));
    fireEvent.click(button("Back to Merge 1 → 2"));
    expect(within(page()).getByRole("heading").textContent).toBe("Merge 1 → 2");
    answerEvent(detail());
    fireEvent.click(
      within(within(page()).getByRole("listbox", { name: "Objects involved" })).getAllByRole(
        "option",
      )[1],
    );
    expect(screen.getByRole("region", { name: "Object page" })).toBeTruthy();
    expect(selected.at(-1)).toBe("2");
    fireEvent.keyDown(screen.getByRole("region", { name: "Object page" }), { key: "Escape" });
    expect(page()).toBeTruthy();
    expect(selected.at(-1)).toBeNull();
    fireEvent.keyDown(page(), { key: "Escape" });
    expect(screen.queryByRole("region", { name: "Event page" })).toBeNull();
    // The row of the page it came from is marked.
    const marked = screen
      .getAllByRole("option")
      .filter((row) => row.getAttribute("aria-selected") === "true");
    expect(marked).toHaveLength(1);
    expect(marked[0].textContent).toContain("merge");
  });

  it("steps to the events either side of it in the list as it is now", () => {
    const { answerEvent, eventAsks } = open(1);
    const edges = () => [button("Previous in list").disabled, button("Next in list").disabled];
    expect(edges()).toEqual([false, false]);
    fireEvent.click(button("Next in list"));
    expect(within(page()).getByRole("heading").textContent).toBe("Rename 7 → 8");
    expect(eventAsks().at(-1)).toMatchObject({ event: 2 });
    expect(edges()).toEqual([false, true]);
    answerEvent(detail({ row: EVENTS[2] }));
  });

  it("shows the frame the lane's matching now puts the event on", () => {
    const { answerEvent, source } = open(0);
    answerEvent(detail({ row: { ...EVENTS[0], frame: 7 } }));
    expect(within(page()).getByText("#7")).toBeTruthy();
    expect(within(page()).getByText("00:00:03.500")).toBeTruthy();
    fireEvent.click(button("Go to frame"));
    expect(source.seek).toHaveBeenCalledWith(7);
  });

  it("opens no object while selecting objects is switched off", () => {
    const shown = mount({ selectable: false });
    showEvents();
    shown.answerEvents(EVENTS);
    fireEvent.click(screen.getAllByRole("option")[0]);
    shown.answerEvent(detail());
    fireEvent.click(
      within(within(page()).getByRole("listbox", { name: "Objects involved" })).getAllByRole(
        "option",
      )[0],
    );
    expect(screen.queryByRole("region", { name: "Object page" })).toBeNull();
    expect(shown.selected.every((entity) => entity === null)).toBe(true);
  });

  it("keeps the lane of its pages when the panel is closed and opened again", () => {
    const shown = mount({}, { second: FRAME });
    fireEvent.change(screen.getByRole("combobox", { name: "Lane" }), { target: { value: "1" } });
    showEvents();
    shown.answerEvents(EVENTS, {}, 1);
    fireEvent.click(
      within(screen.getByRole("listbox", { name: "Events" })).getAllByRole("option")[0],
    );
    expect(shown.eventAsks().at(-1)?.lane).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Back to Events" }));
    fireEvent.click(
      within(screen.getByRole("listbox", { name: "Events" })).getAllByRole("option")[0],
    );
    fireEvent.click(screen.getByRole("button", { name: "Close media tools" }));
    fireEvent.click(screen.getByRole("button", { name: "Open media tools" }));
    expect(page()).toBeTruthy();
    expect(shown.eventAsks().at(-1)?.lane).toBe(1);
  });

  it("closes when the file no longer has the event it was opened for", () => {
    const { answerEvent } = open(0);
    answerEvent(detail({ row: EVENTS[2] }));
    expect(screen.queryByRole("region", { name: "Event page" })).toBeNull();
    expect(screen.getByRole("listbox", { name: "Events" })).toBeTruthy();
    cleanup();
    const again = open(0);
    again.answerEvent(null);
    expect(screen.queryByRole("region", { name: "Event page" })).toBeNull();
  });
});
