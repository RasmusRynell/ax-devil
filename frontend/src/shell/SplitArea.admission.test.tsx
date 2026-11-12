import { useEffect } from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vite-plus/test";
import { FakeSettings } from "../client/fakeSettings";
import type { SessionClientMessage } from "../generated/api";
import type { LaneDisplay } from "../lanes/contract";
import { opened, videoFrame } from "../playback/fakePlayback";
import { flushFrames, installViewerHarness } from "../playback/videoViewerHarness";
import { scenario } from "../session/fakeScenario";
import { FakeSocket } from "../session/fakeSession";
import { viewing } from "../session/fakeViews";
import { SessionProvider } from "../session/session";
import { SessionStore } from "../session/store";
import type { Incoming } from "../stream/wire";
import type { ToWorker } from "../stream/worker";
import { SplitArea } from "./SplitArea";

// Only drawing and the transports are fake: viewers, tickets, links and Session releases stay real.
vi.mock("../playback/VideoCanvas", () => ({
  VideoCanvas: ({ stream }: { stream: LaneDisplay }) => {
    useEffect(() => {
      stream.setSurface({ present: () => {} });
      return () => stream.setSurface(null);
    }, [stream]);
    return <div />;
  },
}));
installViewerHarness();

async function setup() {
  const streams = new Set<number>();
  let requests = 0;
  let refusals = 0;
  let commands = 0;
  let limited = 0;
  // src/session/reader.rs: ten commands at once, replenished by ten per second.
  const BURST = 10;
  const PER_SECOND = 10;
  class AdmissionSocket extends FakeSocket {
    tokens = BURST;
    last = performance.now();
    override send(text: string) {
      const message = JSON.parse(text) as SessionClientMessage;
      if (message.type === "hello") streams.clear(); // Welcome rotates the lease, sweeping older grants.
      if (
        message.type === "cursorOptions" ||
        (message.type === "command" && message.cmd.type !== "setViewport")
      ) {
        const now = performance.now();
        this.tokens = Math.min(BURST, this.tokens + ((now - this.last) * PER_SECOND) / 1000);
        this.last = now;
        if (this.tokens < 1) {
          limited++;
          this.drop(4429);
          return;
        }
        this.tokens--;
      }
      if (message.type === "command") {
        commands++;
        if (message.cmd.type === "releaseStreams")
          for (const viewer of message.cmd.viewers) streams.delete(viewer);
      }
      if (message.type === "command" && message.cmd.type === "requestTicket") {
        for (const viewer of message.cmd.release) streams.delete(viewer);
        streams.delete(message.cmd.key.viewer);
        requests++;
        if (streams.size === 8) {
          refusals++;
          this.emit({
            type: "ack",
            seq: message.seq,
            outcome: { status: "refused", code: "TooManyStreams" },
          });
          return;
        }
        streams.add(message.cmd.key.viewer);
      }
      super.send(text);
    }
  }
  class PlaybackWorker {
    onmessage: ((event: { data: Incoming }) => void) | null = null;
    terminate() {}
    say(data: Incoming) {
      this.onmessage?.({ data });
    }
    postMessage(data: ToWorker) {
      if (data.type === "send" && data.message.type === "Open") {
        this.say({ type: "message", message: opened() });
        this.say({
          type: "envelope",
          envelope: {
            kind: "timeline",
            times: Float64Array.from({ length: 10 }, (_, n) => n * 40_000),
          },
        });
        this.say({ type: "envelope", envelope: { kind: "frame", frame: videoFrame(0) } });
      }
      // A stuck worker never acknowledges close; the Session release owns admission.
    }
  }
  vi.stubGlobal("WebSocket", AdmissionSocket);
  vi.stubGlobal("Worker", PlaybackWorker);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0,
    top: 0,
    width: 800,
    height: 600,
  } as DOMRect);
  const store = new SessionStore();
  render(
    <SessionProvider store={store}>
      <FakeSettings>
        <SplitArea />
      </FakeSettings>
    </SessionProvider>,
  );
  await act(async () => {});
  const scene = scenario("split");
  const room = FakeSocket.rooms.get("room-1")!;
  await act(async () => {
    store.setView((view) => ({
      ...view,
      ...viewing(
        ..."abcdefgh".split("").map((key) => [scene.ids[key], { pinned: key !== "h" }] as const),
      ),
      active: 8,
    }));
    room.show(scene, "eight-open");
  });
  flushFrames();
  expect(streams.size).toBe(8);
  return {
    store,
    streams,
    get commands() {
      return commands;
    },
    get requests() {
      return requests;
    },
    get refusals() {
      return refusals;
    },
    get limited() {
      return limited;
    },
  };
}

it("reopens a preview twice at eight streams even when the old worker never acknowledges close", async () => {
  const state = await setup();
  const pane = () => screen.getByRole("group", { name: "h.mp4" });
  for (let reopen = 0; reopen < 2; reopen++) {
    fireEvent.contextMenu(pane().querySelector("[draggable]")!);
    fireEvent.click(screen.getByRole("menuitem", { name: "Open" }));
  }
  await act(async () => {});
  flushFrames();
  expect(within(pane()).getByRole("button", { name: "Pause" })).toBeTruthy();
  expect([
    state.requests,
    state.streams.size,
    state.refusals,
    state.commands,
    state.limited,
  ]).toEqual([10, 8, 0, 10, 0]);
  expect(screen.queryByRole("alert")).toBeNull();
}, 5000);

it("reconnects eight viewers ready and paused with eight commands and no command-limit close", async () => {
  const state = await setup();
  expect(screen.getAllByRole("button", { name: "Pause" })).toHaveLength(8);
  vi.useFakeTimers();
  act(() => FakeSocket.latest.drop());
  await act(() => vi.advanceTimersByTimeAsync(600));
  flushFrames();
  const commands = FakeSocket.latest.received.filter((message) => message.type === "command");
  expect(commands).toHaveLength(8);
  expect(commands.every((message) => message.cmd.type === "requestTicket")).toBe(true);
  expect(state.limited).toBe(0);
  expect(state.store.getSnapshot().connection).toBe("online");
  expect(screen.getAllByRole("slider", { name: "Timeline" })).toHaveLength(8);
  expect(screen.getAllByRole("button", { name: "Play" })).toHaveLength(8);
}, 5000);
