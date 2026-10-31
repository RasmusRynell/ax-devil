import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PLAYBACK_PROTOCOL, type ClientMessage } from "./protocol";
import { VideoViewer } from "./VideoViewer";
import type { Connect, Incoming } from "./wire";

// The picture needs WebGL; this suite is about the session behind it.
vi.mock("./VideoCanvas", () => ({ VideoCanvas: () => null }));

afterEach(cleanup);

const content = { kind: "SeekableVideo" as const, id: "content-a", display_name: "a.mp4" };

/** A viewer on a fake backend that opens a 100-frame video as soon as it is asked. */
function mount(saved?: unknown) {
  const sent: ClientMessage[] = [];
  const memory = { saved, save: vi.fn() };
  const connect: Connect = (onIncoming) => {
    const say = (incoming: Incoming) => act(() => onIncoming(incoming));
    return {
      send: (message) => {
        sent.push(message);
        if (message.type !== "Open") return;
        say({
          type: "message",
          message: {
            type: "Opened",
            protocol: PLAYBACK_PROTOCOL,
            frame_count: 100,
            duration_us: 4_000_000,
            frame_bytes: 24,
          },
        });
        const times = Float64Array.from({ length: 100 }, (_, id) => id * 40_000);
        say({ type: "envelope", envelope: { kind: "timeline", times } });
      },
      close: () => {},
    };
  };
  const view = render(<VideoViewer content={content} memory={memory} connect={connect} />);
  return { sent, memory, ...view };
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
});
