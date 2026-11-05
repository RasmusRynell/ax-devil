import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { useMemo } from "react";
import { afterEach, beforeEach, vi } from "vite-plus/test";
import { FakeSettings } from "../fakeSettings";
import type { EntryInfo, SettingsState } from "../generated/api";
import { FakeSocket } from "../session/fakeSession";
import { useDoc, useView } from "../session/session";
import { opened } from "./fakeOpened";
import type { ClientMessage, LaneInfo, ServerMessage } from "./protocol";
import { VideoViewer, type VideoViewerProps } from "./VideoViewer";
import type { Connect, Incoming } from "./wire";

// What the video viewer suites share. Each suite mocks "./VideoCanvas" itself (the picture needs WebGL; these suites
// are about the session behind it) and calls installViewerHarness() once at its top level.

let frames: FrameRequestCallback[] = [];
/** Runs the animation frames the viewer has asked for. */
export const flushFrames = () => act(() => frames.splice(0).forEach((frame) => frame(0)));

/** Stubs the browser APIs the viewer needs around each test, and undoes them after. */
export function installViewerHarness() {
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
      frames.push(callback),
    );
    vi.stubGlobal("cancelAnimationFrame", () => {});
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
    FakeSocket.reset();
    frames = [];
    sockets.length = 0;
  });
}

const content = { id: "content-a", generation: 1, display_name: "a.mp4" };
const ENTRIES: Record<string, EntryInfo> = {
  "content-a": { id: "content-a", label: "a.mp4", lanes: ["a.mp4"] },
  "content-b": { id: "content-b", label: "b.mp4", lanes: ["b.mp4"] },
};

/** What the playback sockets the viewer opened can be told, newest last. */
export const sockets: ((incoming: Incoming) => void)[] = [];

/**
 * The video viewer for the entry the window's view names, as a playlist viewer shows it, or the first when the view
 * names none.
 */
export function Viewer({
  sent,
  lanes,
  entryId,
  onHideLane,
  onLaneMatching,
  hidden,
  settings,
}: {
  sent: ClientMessage[];
  lanes?: LaneInfo[];
  /** Shows this entry whatever the view names. */
  entryId?: string;
  onHideLane?: (label: string) => void;
  onLaneMatching?: VideoViewerProps["onLaneMatching"];
  hidden?: VideoViewerProps["hidden"];
  /** The settings the viewer reads; the defaults without. */
  settings?: SettingsState;
}) {
  // One connect for the life of the viewer: a new one would restart playback by itself.
  const connect = useMemo<Connect>(() => open(sent, lanes), [sent, lanes]);
  const [viewers] = useView("viewers");
  const entry = ENTRIES[entryId ?? viewers.a?.entry ?? "content-a"];
  // The viewer opens once the Doc has its item, as the shell does.
  return useDoc()?.workspace.items.length ? (
    <FakeSettings state={settings}>
      <VideoViewer
        item="a"
        content={content}
        entry={entry}
        connect={connect}
        onHideLane={onHideLane}
        onLaneMatching={onLaneMatching}
        hidden={hidden}
      />
    </FakeSettings>
  ) : null;
}

/** A fake playback socket that opens a 100-frame video as soon as it is asked. */
function open(sent: ClientMessage[], lanes?: LaneInfo[]): Connect {
  return (onIncoming) => {
    const say = (incoming: Incoming) => act(() => onIncoming(incoming));
    sockets.push(onIncoming);
    return {
      send: (message) => {
        sent.push(message);
        if (message.type !== "Open") return;
        say({ type: "message", message: opened(100, 24, lanes) });
        const times = Float64Array.from({ length: 100 }, (_, id) => id * 40_000);
        say({ type: "envelope", envelope: { kind: "timeline", times } });
      },
      close: () => {},
    };
  };
}

/** What the backend says on the newest playback socket. */
export const answer = (message: ServerMessage) =>
  act(() => sockets.at(-1)?.({ type: "message", message }));

/** The timeline slider, once the viewer has opened its stream. */
export const slider = () =>
  screen.findByRole("slider", { name: "Timeline" }) as Promise<HTMLInputElement>;
/** The speed button, which shows the speed. */
export const speed = () => screen.getByRole("button", { name: "Speed" });
/** Sets the speed through the speed menu. */
export function setSpeed(value: string) {
  fireEvent.click(speed());
  const field = screen.getByRole("textbox", { name: "Custom" });
  fireEvent.change(field, { target: { value } });
  fireEvent.submit(field);
}
/** The view updates the newest session socket was sent. */
export const viewsSent = () =>
  FakeSocket.latest.received.filter((message) => message.type === "view");
