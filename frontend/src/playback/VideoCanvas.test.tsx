import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, render } from "@testing-library/react";
import type { AtlasMirror } from "../overlay/mirror";
import type { PlaybackSession, Surface } from "./session";
import type { VideoFrame } from "./protocol";
import { VideoCanvas } from "./VideoCanvas";

// The picture needs WebGL; this suite is about where the pointer anchor sits.
vi.mock("./renderer", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./renderer")>()),
  RendererUnavailable: class extends Error {},
  VideoRenderer: class {
    present = vi.fn();
    resize = vi.fn();
    setCells = vi.fn();
    dispose = vi.fn();
  },
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("video canvas", () => {
  it("anchors other windows' pointers to the picture's own shape, once there is one", () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    let surface: Surface | null = null;
    const session = {
      setSurface: (next: Surface | null) => (surface = next),
    } as unknown as PlaybackSession;
    const view = render(<VideoCanvas session={session} label="a.mp4" anchor="frame:a" />);
    const anchor = () => view.container.querySelector<HTMLElement>('[data-cursor-id="frame:a"]');
    expect(anchor()).toBeNull();

    const present = (width: number, height: number) =>
      act(() => surface?.present({ width, height } as VideoFrame, [], {} as AtlasMirror));
    present(1920, 1080);
    expect(anchor()?.style.aspectRatio).toBe("1920 / 1080");
    present(1080, 1920);
    expect(anchor()?.style.aspectRatio).toBe("1080 / 1920");
  });

  it("anchors a pointer in each lane cell to the picture as fitted there", () => {
    let surface: Surface | null = null;
    const session = {
      setSurface: (next: Surface | null) => (surface = next),
    } as unknown as PlaybackSession;
    const cells = [
      { lane: 0, rect: { x: 0, y: 0, width: 300, height: 100 } },
      { lane: 2, rect: { x: 300, y: 0, width: 100, height: 100 } },
    ];
    const view = render(
      <VideoCanvas session={session} label="a.mp4" anchor="frame:a" cells={cells} />,
    );
    act(() => surface?.present({ width: 400, height: 200 } as VideoFrame, [], {} as AtlasMirror));
    const at = (lane: number) =>
      view.container.querySelector<HTMLElement>(`[data-cursor-id="frame:a:${lane}"]`);
    expect(at(0)?.style).toMatchObject({
      left: "50px",
      top: "0px",
      width: "200px",
      height: "100px",
    });
    expect(at(2)?.style).toMatchObject({
      left: "300px",
      top: "25px",
      width: "100px",
      height: "50px",
    });
  });
});
