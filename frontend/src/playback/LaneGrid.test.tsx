import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, render, screen } from "@testing-library/react";
import { hiddenOf } from "../viewer/hidden";
import { LANE_DEFAULTS } from "./fakeOpened";
import { columnsFor, displayedSizes, LaneGrid, layoutLanes } from "./LaneGrid";
import type { LaneState } from "./lanes";
import type { PlaybackSession } from "./session";
import type { PlaybackView } from "./view";

// The picture needs WebGL; the cells are what the grid hands it.
vi.mock("./VideoCanvas", () => ({ VideoCanvas: () => null }));

afterEach(cleanup);

describe("lane grid math", () => {
  it("puts one or two lanes side by side and more in the smallest square that holds them", () => {
    expect([0, 1, 2, 3, 4, 5, 9, 10].map(columnsFor)).toEqual([0, 1, 2, 2, 2, 3, 3, 4]);
  });

  it("tiles the whole area with whole pixels that neighbours share", () => {
    const cells = layoutLanes([0, 1, 2, 3], 101, 51);
    expect(cells.map(({ rect }) => rect)).toEqual([
      { x: 0, y: 0, width: 51, height: 26 },
      { x: 51, y: 0, width: 50, height: 26 },
      { x: 0, y: 26, width: 51, height: 25 },
      { x: 51, y: 26, width: 50, height: 25 },
    ]);
  });

  it("shares the width of a short last row among its lanes, and keeps lane indices", () => {
    const cells = layoutLanes([4, 7, 9, 2, 5], 300, 200);
    expect(cells.map(({ lane }) => lane)).toEqual([4, 7, 9, 2, 5]);
    expect(cells.slice(3).map(({ rect }) => [rect.x, rect.width])).toEqual([
      [0, 150],
      [150, 150],
    ]);
  });

  it("sizes the picture in each cell by its aspect, and gives hidden lanes none", () => {
    const cells = layoutLanes([0, 2], 800, 300);
    expect(displayedSizes(cells, 3, { width: 640, height: 480 })).toEqual([
      { width: 400, height: 300 },
      { width: 0, height: 0 },
      { width: 400, height: 300 },
    ]);
  });
});

function lane(label: string, visible = true): LaneState {
  return {
    label,
    hasOverlay: false,
    status: "plain",
    view: { ...LANE_DEFAULTS, visible },
    revision: 0,
  };
}

function mount(labels: string[], hiddenLabels: string[], views: Record<string, boolean> = {}) {
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
  const session = { setLaneView: vi.fn(), setLaneTargets: vi.fn() };
  const view = {
    lanes: labels.map((label) => lane(label, views[label] ?? true)),
    width: 640,
    height: 480,
  } as unknown as PlaybackView;
  const hidden = hiddenOf(
    hiddenLabels.map((label) => ({
      key: `e\u001f${label}`,
      kind: "lane" as const,
      label,
      status: { status: "Ready" as const },
      hidden: true,
      entry: "e",
    })),
  );
  act(() => {
    render(
      <LaneGrid
        session={session as unknown as PlaybackSession}
        view={view}
        name="clip"
        anchor="frame:i"
        entry="e"
        hidden={hidden}
        onHide={() => {}}
      />,
    );
  });
  return session;
}

describe("lane grid", () => {
  it("leaves a hidden lane out, stops its overlay and gives the rest the room", () => {
    const session = mount(["A", "B", "C"], ["B"]);
    expect(screen.getByText("A")).toBeTruthy();
    expect(screen.getByText("C")).toBeTruthy();
    expect(screen.queryByText("B")).toBeNull();
    expect(session.setLaneView.mock.calls).toEqual([[1, { visible: false }]]);
    expect(session.setLaneTargets.mock.lastCall?.[0]).toEqual([
      { width: 400, height: 300 },
      { width: 0, height: 0 },
      { width: 400, height: 300 },
    ]);
  });

  it("shows a lane again once it is no longer hidden", () => {
    const session = mount(["A", "B"], [], { B: false });
    expect(screen.getByText("B")).toBeTruthy();
    expect(session.setLaneView.mock.calls).toEqual([[1, { visible: true }]]);
  });

  it("says so when every lane is hidden", () => {
    mount(["A"], ["A"]);
    expect(screen.getByRole("status").textContent).toBe("All lanes are hidden");
  });
});
