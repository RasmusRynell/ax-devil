import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { NOTHING_HIDDEN } from "../viewer/hidden";
import { LANE_DEFAULTS, opened } from "./fakeOpened";
import type { ClientMessage, LaneInfo, ServerMessage } from "./protocol";
import { VideoViewer } from "./VideoViewer";
import type { Connect, Incoming } from "./wire";

// The picture needs WebGL; this suite is about the session behind it.
vi.mock("./VideoCanvas", () => ({ VideoCanvas: () => null }));

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const content = {
  kind: "SeekableVideo" as const,
  id: "content-a",
  display_name: "a.mp4",
  entry: { id: "content-a", label: "a.mp4", lanes: ["a.mp4"] },
};

/** A viewer on a fake backend that opens a 100-frame video as soon as it is asked. */
function mount(
  saved?: unknown,
  lanes: LaneInfo[] = [{ label: "a.mp4", has_overlay: false }],
  hidden = NOTHING_HIDDEN,
) {
  const sent: ClientMessage[] = [];
  const memory = { saved, save: vi.fn() };
  let incoming: ((incoming: Incoming) => void) | undefined;
  const connect: Connect = (onIncoming) => {
    incoming = onIncoming;
    // The backend answers from the session's start effect, which `render` already runs inside `act`; a nested `act`
    // there ends the outer one's scope early and leaves the viewer's own state update outside it.
    const say = onIncoming;
    return {
      send: (message) => {
        sent.push(message);
        if (message.type !== "Open") return;
        say({
          type: "message",
          message: opened(100, 24, lanes),
        });
        const times = Float64Array.from({ length: 100 }, (_, id) => id * 40_000);
        say({ type: "envelope", envelope: { kind: "timeline", times } });
      },
      close: () => {},
    };
  };
  const answer = (message: ServerMessage) => act(() => incoming?.({ type: "message", message }));
  const view = render(
    <VideoViewer content={content} memory={memory} hidden={hidden} connect={connect} />,
  );
  return { sent, memory, answer, ...view };
}

describe("video viewer", () => {
  it("saves where it was and at what speed when it goes away, and opens there when shown again", async () => {
    const first = mount();
    const bar = await screen.findByRole("slider", { name: "Timeline" });
    fireEvent.change(bar, { target: { value: "37" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Speed" }), {
      target: { value: "2.5" },
    });
    first.unmount();
    expect(first.memory.save).toHaveBeenCalledWith({ frame: 37, speed: 2.5 });

    const second = mount({ frame: 37, speed: 2.5 });
    expect(
      ((await screen.findByRole("slider", { name: "Timeline" })) as HTMLInputElement).value,
    ).toBe("37");
    expect((screen.getByRole("spinbutton", { name: "Speed" }) as HTMLInputElement).value).toBe(
      "2.5",
    );
    expect(second.sent[0]).toEqual({ type: "Open", content_id: "content-a", start_frame: 37 });
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
  });

  it("sends the lane menu's changes to the backend as the lane's new view", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ catalogs: [], default: "" }) })),
    );
    const { sent, answer } = mount(undefined, [
      { label: "DPM", has_overlay: true },
      { label: "GT", has_overlay: true },
    ]);
    await screen.findByRole("slider", { name: "Timeline" });
    const filter = [{ id: "pedestrian", label: "Pedestrian", default_enabled: true }];
    answer({
      type: "LaneStatus",
      lane: 1,
      status: { status: "Ready", clock: "frame", filter, warnings: [], samples: 4 },
    });
    fireEvent.click(screen.getByRole("button", { name: "GT: settings" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Pedestrian" }));
    expect(sent.at(-1)).toEqual({
      type: "LaneView",
      lane: 1,
      revision: 1,
      view: { ...LANE_DEFAULTS, filter: { pedestrian: false } },
    });
  });

  it("hides a lane through the backend, which keeps hidden state", async () => {
    const fetched = vi.fn(async () => ({ ok: true, json: async () => ({}) }));
    vi.stubGlobal("fetch", fetched);
    mount(undefined, [{ label: "DPM", has_overlay: true }]);
    await screen.findByRole("slider", { name: "Timeline" });
    fireEvent.click(screen.getByRole("button", { name: "DPM: settings" }));
    fireEvent.click(screen.getByRole("button", { name: "Hide lane" }));
    expect(fetched).toHaveBeenLastCalledWith("/api/workspace/hidden", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: { entry: "content-a", lane: "DPM" }, hidden: true }),
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("lists a lane's warnings on its tag", async () => {
    const { answer } = mount(undefined, [{ label: "DPM", has_overlay: true }]);
    await screen.findByRole("slider", { name: "Timeline" });
    answer({
      type: "LaneStatus",
      lane: 0,
      status: {
        status: "Ready",
        clock: "frame",
        filter: [],
        warnings: [
          {
            problem: "Skipped 2 rows",
            reasons: ["row 4"],
            consequence: "They are not drawn",
            more_reasons: true,
          },
        ],
        samples: 4,
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "DPM: warnings" }));
    const panel = screen.getByRole("dialog", { name: "DPM: warnings" });
    expect(panel.textContent).toContain("Skipped 2 rows");
    expect(panel.textContent).toContain("They are not drawn");
    expect(panel.textContent).toContain("row 4");
  });
});
