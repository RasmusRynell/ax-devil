import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, render } from "@testing-library/react";
import type { PlaybackSession, Surface } from "./session";
import type { VideoFrame } from "./protocol";
import { VideoCanvas } from "./VideoCanvas";

// The picture needs WebGL; this suite is about where the pointer anchor sits.
vi.mock("./renderer", () => ({
  RendererUnavailable: class extends Error {},
  VideoRenderer: class {
    present = vi.fn();
    resize = vi.fn();
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
      act(() => surface?.present({ width, height } as VideoFrame));
    present(1920, 1080);
    expect(anchor()?.style.aspectRatio).toBe("1920 / 1080");
    present(1080, 1920);
    expect(anchor()?.style.aspectRatio).toBe("1080 / 1920");
  });
});
