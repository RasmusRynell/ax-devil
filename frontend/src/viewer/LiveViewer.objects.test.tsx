import { act, render, screen } from "@testing-library/react";
import type { ComponentProps, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { DEFAULT_SETTINGS, FakeSettings } from "../client/fakeSettings";
import type { Picked } from "../playback/contract";
import { LANE_DEFAULTS } from "../playback/fakeOpened";
import { PLAYBACK_PROTOCOL } from "../playback/protocol";
import type { Geometry } from "../playback/renderer";
import type { VideoCanvas } from "../playback/VideoCanvas";
import type { Connect, Incoming } from "../playback/wire";
import { scenario } from "../session/fakeScenario";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider, useDoc } from "../session/session";
import { LiveVideoViewer } from "./LiveViewer";
import { NO_ACTIONS, ViewerActionsContext, type ViewerActions } from "./actions";
import { useFrontViewer } from "./useViewers";

// What the canvas was last given; the picture needs WebGL, and this suite is about what the live viewer does with it.
const canvas = vi.hoisted(() => ({
  props: undefined as ComponentProps<typeof VideoCanvas> | undefined,
}));
vi.mock("../playback/VideoCanvas", () => ({
  VideoCanvas: (props: ComponentProps<typeof VideoCanvas>) => {
    canvas.props = props;
    return null;
  },
}));

const SELECTION = { lane: 0, source: 0, entity: "7" };
const PICKED: Picked = {
  lane: 0,
  source: 0,
  sample: 0,
  row: 0,
  entity: "7",
  label: "7 car",
  detail: "score 0.82",
  category: 5,
  box: { x: 10, y: 20, width: 30, height: 40 },
};

const registry = { current: NO_ACTIONS };
const offer = (next: ViewerActions | ((now: ViewerActions) => ViewerActions)) => {
  registry.current = typeof next === "function" ? next(registry.current) : next;
};

let tell: (incoming: Incoming) => void = () => {};
const connect: Connect = (onIncoming) => {
  tell = onIncoming;
  return { send: () => {}, close: () => {} };
};

function Viewer(): ReactNode {
  useFrontViewer();
  const item = useDoc()?.workspace.items[0];
  const content = item?.resolution.status === "Resolved" ? item.resolution.content : undefined;
  return item && content?.kind === "LiveVideo" ? (
    <FakeSettings state={DEFAULT_SETTINGS}>
      <LiveVideoViewer item={item.id} viewer={1} content={content} connect={connect} />
    </FakeSettings>
  ) : null;
}

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("WebSocket", FakeSocket);
});
afterEach(() => {
  vi.unstubAllGlobals();
  FakeSocket.reset();
});

/** The geometry of what is drawn: it reports the outlines of the picked objects. */
const geometry = () =>
  ({
    zoomAt: vi.fn(),
    boxes: vi.fn(() => []),
    reference: () => undefined,
  }) as unknown as Geometry & {
    boxes: ReturnType<typeof vi.fn>;
  };

describe("live viewer objects", () => {
  it("outlines an object picked on the live video and shows its page in the media tools, and a hovered object gets its chip", async () => {
    render(
      <SessionProvider>
        <ViewerActionsContext.Provider value={offer}>
          <Viewer />
        </ViewerActionsContext.Provider>
      </SessionProvider>,
    );
    await act(async () => {});
    act(() => FakeSocket.rooms.get("room-1")?.show(scenario("live-video")));
    await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
    act(() =>
      tell({
        type: "message",
        message: {
          type: "LiveOpened",
          protocol: PLAYBACK_PROTOCOL,
          lane: { label: "Objects", slot: "overlay", source: 0 },
          lane_defaults: LANE_DEFAULTS,
          catalogs: [],
        },
      }),
    );
    const props = () => canvas.props as ComponentProps<typeof VideoCanvas>;
    const shown = geometry();
    act(() => props().onDrawn?.(shown));

    act(() => props().onHover?.(PICKED));
    expect(screen.getByText("7 car")).toBeTruthy();
    act(() => props().onHover?.(null));
    expect(screen.queryByText("7 car")).toBeNull();

    act(() => props().onSelect?.(SELECTION));
    expect(shown.boxes).toHaveBeenLastCalledWith(SELECTION, null);
    act(() => registry.current["media-tools"]?.());
    expect(screen.getByRole("region", { name: "Object page" })).toBeTruthy();

    // Empty space on the video drops the outline, not the page.
    act(() => props().onSelect?.(null));
    expect(shown.boxes).toHaveBeenLastCalledWith(null, null);
    expect(screen.getByRole("region", { name: "Object page" })).toBeTruthy();
  });
});
