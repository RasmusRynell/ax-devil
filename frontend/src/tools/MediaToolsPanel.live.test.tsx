import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { EVENT_LOG_CAP, EventLog } from "../live/eventLog";
import { mount, says, showEvents } from "./panelRig";

beforeEach(() => vi.stubGlobal("ResizeObserver", undefined));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const US = 1_700_000_000_000_000;
const event = (kind: "rename" | "merge", entities: string[]) => ({
  kind,
  label: `${kind === "rename" ? "Rename" : "Merge"} ${entities.join(" → ")}`,
  entities,
});

/** A live stream's log with a rename of 7 and a merge on frame 0, and a stretch of lost samples after. */
function logged() {
  const log = new EventLog();
  log.addEvents({
    frame: 0,
    samples: [{ time_us: US, events: [event("rename", ["7", "99"]), event("merge", ["12", "5"])] }],
  });
  log.addGap(US + 40_000);
  return log;
}
const rows = () =>
  within(screen.getByRole("listbox", { name: "Events" }))
    .getAllByRole("option")
    .map((row) => row.textContent);

describe("a live stream's Events list", () => {
  it("lists what the log kept, oldest first, with a marker where samples were lost", () => {
    mount({ log: logged() });
    showEvents();
    // The tab counts events, not the marker.
    expect(screen.getByRole("tab", { name: "Events 2" })).toBeTruthy();
    expect(rows()).toEqual([
      "22:13:20.000rename7, 99",
      "22:13:20.000merge12, 5",
      "22:13:20.040samples lost",
    ]);
    // Both events are of the frame on screen.
    const current = screen.getAllByRole("option").map((row) => row.getAttribute("aria-current"));
    expect(current).toEqual(["true", "true", null]);
  });

  it("searches here, and says how many events were dropped past the cap", () => {
    const log = logged();
    log.addEvents({
      frame: 0,
      samples: [
        {
          time_us: US,
          events: Array.from({ length: EVENT_LOG_CAP }, () => event("rename", ["1", "2"])),
        },
      ],
    });
    mount({ log });
    showEvents();
    expect(says("3 older dropped")).toBe(true);
    const box = screen.getByRole("textbox", { name: "Search events" });
    fireEvent.change(box, { target: { value: "kind:merge" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(screen.getByText("No matching events")).toBeTruthy();
  });

  it("opens an event's page with its objects as the frame on screen has them, and nothing to seek", () => {
    mount({ log: logged() });
    showEvents();
    fireEvent.click(screen.getAllByRole("option")[0]);
    const page = screen.getByRole("region", { name: "Event page" });
    expect(within(page).getAllByRole("heading")[0].textContent).toBe("Rename 7 → 99");
    expect(within(page).queryByRole("button", { name: "Go to frame" })).toBeNull();
    expect(within(page).queryByRole("region", { name: "Same moment" })).toBeNull();
    const objects = within(within(page).getByRole("listbox", { name: "Objects involved" }));
    // Object 7 is in the frame and has its class; 99 is in no frame.
    expect(objects.getAllByRole("option").map((row) => row.textContent)).toEqual(["7car", "99"]);
    fireEvent.keyDown(page, { key: "Escape" });
    expect(screen.getByRole("listbox", { name: "Events" })).toBeTruthy();
  });
});
