import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, render, screen } from "@testing-library/react";
import { FakeSettings } from "../client/fakeSettings";
import { scenario } from "../session/fakeScenario";
import { hiddenOf } from "../viewer/hidden";
import { LANE_SETTINGS } from "./fakeOpened";
import { LaneGrid } from "./LaneGrid";
import { columnsFor, layoutLanes } from "./laneCells";
import type { LaneState } from "./lanes";
import type { PlaybackSession } from "./session";
import type { PlaybackView } from "./view";

// The picture needs WebGL; the cells are what the grid hands it.
vi.mock("./VideoCanvas", () => ({ VideoCanvas: () => null }));

afterEach(cleanup);

// Entry A of the playlist `mot` has the lanes GT and DPM; its DPM lane is hidden, or neither, or both.
const tree = { source: scenario("tree"), entry: "mot/A" };
// Entry E of the playlist `grid` has the lanes GT, DPM and SEG.
const grid = { source: scenario("playlist-lanes"), entry: "grid/E" };

describe("lane grid math", () => {
  it("puts one or two lanes side by side and more in the smallest square that holds them", () => {
    expect([0, 1, 2, 3, 4, 5, 9, 10].map(columnsFor)).toEqual([0, 1, 2, 2, 2, 3, 3, 4]);
  });

  it("spans the whole area with whole pixels and a gap of exactly the gutter between neighbours", () => {
    const [a, b, c, d] = layoutLanes([0, 1, 2, 3], 101, 51, 2).map(({ rect }) => rect);
    for (const rect of [a, b, c, d])
      for (const edge of [rect.x, rect.y, rect.width, rect.height])
        expect(Number.isInteger(edge)).toBe(true);
    expect([a.x, a.y, c.x, b.y]).toEqual([0, 0, 0, 0]);
    expect([b.x + b.width, d.x + d.width, c.y + c.height, d.y + d.height]).toEqual([
      101, 101, 51, 51,
    ]);
    expect([b.x - (a.x + a.width), d.x - (c.x + c.width)]).toEqual([2, 2]);
    expect([c.y - (a.y + a.height), d.y - (b.y + b.height)]).toEqual([2, 2]);
  });

  it("leaves no cell of negative size when the area is smaller than the gutters", () => {
    for (const rect of layoutLanes([0, 1, 2, 3], 0, 0, 2).map(({ rect }) => rect))
      expect(Math.min(rect.width, rect.height)).toBeGreaterThanOrEqual(0);
  });

  it("keeps the column widths in a short last row, and keeps lane indices", () => {
    const cells = layoutLanes([4, 7, 9, 2, 5], 308, 200, 4);
    expect(cells.map(({ lane }) => lane)).toEqual([4, 7, 9, 2, 5]);
    expect(cells.slice(3).map(({ rect }) => [rect.x, rect.width])).toEqual([
      [0, 100],
      [104, 100],
    ]);
  });
});

function lane(label: string, visible = true): LaneState {
  return {
    label,
    slot: label,
    source: null,
    status: "plain",
    settings: { ...LANE_SETTINGS, visible },
    policyRevision: 0,
    pending: false,
  };
}

function mount(
  labels: string[],
  state: string,
  views: Record<string, boolean> = {},
  frame = { width: 0, height: 0 },
  title?: string,
  at = tree,
) {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(private readonly callback: (entries: unknown[]) => void) {}
      observe() {
        this.callback([{ contentRect: { width: 800, height: 300 } }]);
      }
      disconnect() {}
    },
  );
  const session = { setLane: vi.fn() };
  const view = {
    lanes: labels.map((label) => lane(label, views[label] ?? true)),
    ...frame,
  } as unknown as PlaybackView;
  const hidden = hiddenOf(at.source.doc(state).workspace.rows);
  act(() => {
    render(
      <FakeSettings>
        <LaneGrid
          session={session as unknown as PlaybackSession}
          view={view}
          name="clip"
          title={title}
          anchor="frame:i"
          entry={at.source.ids[at.entry]}
          hidden={hidden}
          onHide={() => {}}
        />
      </FakeSettings>,
    );
  });
  return session;
}

describe("lane grid", () => {
  it("leaves a hidden lane out, stops its data and gives the rest the room", () => {
    const session = mount(["GT", "DPM", "SEG"], "dpm-hidden", {}, undefined, undefined, grid);
    expect(screen.getByText("GT")).toBeTruthy();
    expect(screen.getByText("SEG")).toBeTruthy();
    expect(screen.queryByText("DPM")).toBeNull();
    expect(session.setLane.mock.calls).toEqual([[1, { visible: false }]]);
  });

  it("shows a lane again once it is no longer hidden", () => {
    const session = mount(["GT", "DPM"], "dpm-shown", { DPM: false });
    expect(screen.getByText("DPM")).toBeTruthy();
    expect(session.setLane.mock.calls).toEqual([[1, { visible: true }]]);
  });

  it("puts a lane's tag at the corner of the visible picture, not of the cell around it", () => {
    // A 4:3 video in an 800 x 300 cell is 400 wide with 200 pixels of bar on each side; the tag may run over the right bar.
    mount(["GT"], "dpm-shown", {}, { width: 400, height: 300 });
    const tag = screen.getByText("GT").closest(".absolute") as HTMLElement;
    expect([tag.style.left, tag.style.top, tag.style.maxWidth]).toEqual(["200px", "0px", "600px"]);
  });

  it("leaves out the name of a lane that is the only one shown and is named as the viewer", () => {
    mount(["GT", "DPM"], "initial", {}, undefined, "GT");
    expect(screen.queryByText("GT")).toBeNull();
    cleanup();
    mount(["GT", "DPM"], "dpm-shown", {}, undefined, "GT");
    expect(screen.getByText("GT")).toBeTruthy();
  });

  it("says so when every lane is hidden", () => {
    mount(["GT"], "gt-hidden");
    expect(screen.getByRole("status").textContent).toBe("All lanes are hidden");
  });
});
