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

it("reopens a preview twice at eight streams even when the old worker never acknowledges close", async () => {
  const streams = new Set<number>();
  let requests = 0;
  let refusals = 0;
  let workers = 0;
  class AdmissionSocket extends FakeSocket {
    override send(text: string) {
      const message = JSON.parse(text) as SessionClientMessage;
      if (message.type === "command" && message.cmd.type === "releaseStream")
        streams.delete(message.cmd.viewer);
      if (message.type === "command" && message.cmd.type === "requestTicket") {
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
    readonly id = ++workers;
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
  const pane = () => screen.getByRole("group", { name: "h.mp4" });
  for (let reopen = 0; reopen < 2; reopen++) {
    fireEvent.contextMenu(pane().querySelector("[draggable]")!);
    fireEvent.click(screen.getByRole("menuitem", { name: "Open" }));
  }
  await act(async () => {});
  flushFrames();
  expect(within(pane()).getByRole("button", { name: "Pause" })).toBeTruthy();
  expect([requests, streams.size, refusals]).toEqual([10, 8, 0]);
  expect(screen.queryByRole("alert")).toBeNull();
}, 5000);
