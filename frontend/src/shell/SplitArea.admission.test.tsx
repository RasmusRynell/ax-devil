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

// Only drawing and the transports are fake: viewers, tickets, links and worker-close completion stay real.
vi.mock("../playback/VideoCanvas", () => ({
  VideoCanvas: ({ stream }: { stream: LaneDisplay }) => {
    useEffect(() => {
      stream.setSurface({ present: () => {} });
      return () => stream.setSurface(null);
    }, [stream]);
    return <div />;
  },
}));
function barrier() {
  let resolve = () => {};
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
installViewerHarness();

it("reopens a preview at eight streams only after its predecessor releases its grant", async () => {
  const release = barrier();
  const closing = barrier();
  const replacement = barrier();
  let grants = 0;
  let requests = 0;
  let refusals = 0;
  let workers = 0;
  class AdmissionSocket extends FakeSocket {
    override send(text: string) {
      const message = JSON.parse(text) as SessionClientMessage;
      if (message.type === "command" && message.cmd.type === "requestTicket") {
        requests++;
        if (grants === 8) {
          refusals++;
          this.emit({
            type: "ack",
            seq: message.seq,
            outcome: { status: "refused", code: "TooManyStreams" },
          });
          return;
        }
        grants++;
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
        if (this.id === 9) replacement.resolve();
      } else if (data.type === "close") {
        const done = this.id === 8 ? release.promise : Promise.resolve();
        if (this.id === 8) closing.resolve();
        void done.then(() => {
          grants--;
          this.say({ type: "closed", code: 1000 });
        });
      }
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
  expect(grants).toBe(8);
  const pane = () => screen.getByRole("group", { name: "h.mp4" });
  fireEvent.contextMenu(pane().querySelector("[draggable]")!);
  fireEvent.click(screen.getByRole("menuitem", { name: "Open" }));
  await act(async () => {
    await closing.promise;
  });
  expect(requests).toBe(8);
  expect(within(pane()).getByRole("status").textContent).toBe("Opening h.mp4…");
  expect(screen.queryByRole("alert")).toBeNull();
  await act(async () => {
    release.resolve();
    await replacement.promise;
  });
  flushFrames();
  expect(within(pane()).getByRole("button", { name: "Pause" })).toBeTruthy();
  expect([requests, grants, refusals]).toEqual([9, 8, 0]);
  expect(screen.queryByRole("alert")).toBeNull();
}, 5000);
