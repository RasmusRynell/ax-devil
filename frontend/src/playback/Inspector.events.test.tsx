import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vite-plus/test";
import type { EventRowWire } from "../generated/api";
import { found } from "../inspect/fakeFileAnswer";
import { LANE_SETTINGS } from "../lanes/fakeOpened";
import { FRESH_MEMORY } from "../inspect/fields";
import { FileScope } from "../inspect/fileScope";
import { Inspector, type Reach } from "./Inspector";
import { LaneSet } from "../lanes/lanes";
import type { ClientMessage } from "../stream/protocol";

afterEach(cleanup);

/** Frames 40 ms apart, the first at 01:02:03.004 into the day. */
const timeOf = (frame: number) => 3_723_004_000 + frame * 40_000;

const event = (index: number, frame: number): EventRowWire => ({
  anchor: Array(32).fill(0) as EventRowWire["anchor"],
  event: index,
  frame,
  kind: "merge",
  label: `Merge 7 → ${index}`,
  objects: [{ id: "7", class: "car" }],
});

it("marks the object's events on its strip, lists them under its fields and opens one, saying when there are more", () => {
  for (const truncated of [false, true]) {
    const lanes = new LaneSet();
    lanes.open([{ label: "DPM", slot: "DPM", source: 0 }], LANE_SETTINGS);
    lanes.setStatus(0, {
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
    });
    const sent: ClientMessage[] = [];
    const files = new FileScope(
      lanes,
      (message) => sent.push(message),
      () => () => {},
    );
    const onEvent = vi.fn();
    const reach: Reach = { source: files, frame: 0, frames: 100, timeOf, seek: vi.fn() };
    render(
      <Inspector
        inspection={{
          state: "ready",
          selection: { lane: 0, source: 0, entity: "7" },
          summary: { entity: "7", className: "car", score: 0.8, confidence: null, motion: null },
          fields: [],
          truncated: false,
        }}
        memory={FRESH_MEMORY}
        onMemory={() => {}}
        reach={reach}
        onEvent={onEvent}
      />,
    );
    const ask = sent.find((message) => message.type === "Appearances") as { request: number };
    act(() =>
      files.receive(
        found(ask.request, {
          kind: "Appearances",
          entity: "7",
          spans: [[10, 30]],
          truncated: false,
          // Two events share a frame.
          events: [event(3, 10), event(4, 20), event(5, 20)],
          events_truncated: truncated,
        }),
      ),
    );
    const strip = screen.getByRole("img", { name: "Presence" });
    expect(
      [...strip.querySelectorAll("[data-tick]")].map((tick) => tick.getAttribute("data-tick")),
    ).toEqual(["10", "20"]);
    const section = screen.getByRole("region", { name: "Events" });
    expect(section.textContent?.includes("more not shown")).toBe(truncated);
    const events = within(section);
    expect(events.getAllByRole("option").map((row) => row.textContent)).toEqual([
      "01:02:03.404merge7 → 3",
      "01:02:03.804merge7 → 4",
      "01:02:03.804merge7 → 5",
    ]);
    fireEvent.click(events.getAllByRole("option")[1]);
    expect(onEvent).toHaveBeenCalledWith(event(4, 20), 1);
    cleanup();
  }
});
