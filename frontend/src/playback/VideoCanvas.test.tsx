import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import type { Picked, Viewport } from "./contract";
import type { PlaybackSession, Surface } from "./session";
import type { VideoFrame } from "./protocol";
import { VideoCanvas } from "./VideoCanvas";
import { FIT, ZOOM_STEP } from "./viewport";

// The picture needs WebGL; this suite is about where the pointer anchor sits and what the pointer asks for.
const drawn = vi.hoisted(() => ({
  renderer: null as null | Record<string, ReturnType<typeof vi.fn>>,
}));
vi.mock("./renderer", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./renderer")>()),
  RendererUnavailable: class extends Error {},
  VideoRenderer: class {
    present = vi.fn();
    resize = vi.fn();
    setCells = vi.fn();
    setViewport = vi.fn();
    redraw = vi.fn();
    pick = vi.fn(() => null);
    zoomAt = vi.fn();
    panBy = vi.fn();
    dispose = vi.fn();
    constructor() {
      drawn.renderer = this as unknown as typeof drawn.renderer;
    }
  },
}));

beforeEach(() => {
  // jsdom has neither media queries nor pointer capture.
  vi.stubGlobal("matchMedia", () => ({ addEventListener() {}, removeEventListener() {} }));
  HTMLElement.prototype.setPointerCapture = () => {};
  HTMLElement.prototype.releasePointerCapture = () => {};
});

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
      act(() => surface?.present({ width, height } as VideoFrame, []));
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
    act(() => surface?.present({ width: 400, height: 200 } as VideoFrame, []));
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

  it("lays a lane's anchor over its zoomed picture, cut to the cell", () => {
    let surface: Surface | null = null;
    const session = {
      setSurface: (next: Surface | null) => (surface = next),
    } as unknown as PlaybackSession;
    const cells = [{ lane: 0, rect: { x: 0, y: 0, width: 400, height: 200 } }];
    const view = render(
      <VideoCanvas
        session={session}
        label="a.mp4"
        anchor="frame:a"
        cells={cells}
        viewport={{ zoom: 2, panX: 0, panY: 0 }}
      />,
    );
    act(() => surface?.present({ width: 400, height: 200 } as VideoFrame, []));
    const anchor = view.container.querySelector<HTMLElement>('[data-cursor-id="frame:a:0"]');
    expect(anchor?.style).toMatchObject({
      left: "-200px",
      top: "-100px",
      width: "800px",
      height: "400px",
    });
    expect(anchor?.style.clipPath).toBe("inset(100px 200px 100px 200px)");
    // Reaching past the cell must not make the page scroll: the surface clips what its zoomed pictures overflow.
    expect(anchor?.closest(".overflow-clip")).toBe(anchor?.parentElement?.parentElement);
  });

  describe("pointer", () => {
    const session = { setSurface: () => {} } as unknown as PlaybackSession;
    const picked = (entity: string): Picked => ({
      lane: 0,
      source: 3,
      sample: 5,
      row: 1,
      entity,
      label: `${entity} car`,
      detail: "",
      category: 5,
      box: { x: 1, y: 2, width: 3, height: 4 },
    });
    const zoomed: Viewport = { zoom: 2, panX: 0, panY: 0 };
    function mount(props: Partial<React.ComponentProps<typeof VideoCanvas>> = {}) {
      const handlers = { onViewport: vi.fn(), onHover: vi.fn(), onSelect: vi.fn() };
      const view = render(
        <VideoCanvas session={session} label="a.mp4" anchor="frame:a" {...handlers} {...props} />,
      );
      return { ...handlers, surface: view.container.firstElementChild as HTMLElement };
    }

    it("zooms toward the pointer by one step per wheel notch", () => {
      const { surface, onViewport } = mount();
      drawn.renderer?.zoomAt.mockReturnValue(zoomed);
      fireEvent.wheel(surface, { deltaY: -100, clientX: 30, clientY: 40 });
      expect(drawn.renderer?.zoomAt).toHaveBeenCalledWith(FIT, ZOOM_STEP, { x: 30, y: 40 });
      expect(onViewport).toHaveBeenCalledWith(zoomed);
    });

    it("pans by a drag, selects by a click that did not drag and resets by a double click", () => {
      const { surface, onViewport, onSelect } = mount({ viewport: zoomed });
      const panned = { zoom: 2, panX: 0.1, panY: 0 };
      drawn.renderer?.panBy.mockReturnValue(panned);
      fireEvent.pointerDown(surface, { button: 0, clientX: 10, clientY: 10 });
      fireEvent.pointerMove(surface, { clientX: 12, clientY: 10 });
      expect(onViewport).not.toHaveBeenCalled();
      fireEvent.pointerMove(surface, { clientX: 30, clientY: 15 });
      expect(drawn.renderer?.panBy).toHaveBeenCalledWith(zoomed, { x: 10, y: 10 }, 20, 5);
      expect(onViewport).toHaveBeenCalledWith(panned);
      fireEvent.pointerUp(surface);
      fireEvent.click(surface, { detail: 1 });
      expect(onSelect).not.toHaveBeenCalled();

      drawn.renderer?.pick.mockReturnValue(picked("t7"));
      fireEvent.pointerDown(surface, { button: 0, clientX: 10, clientY: 10 });
      fireEvent.pointerUp(surface);
      fireEvent.click(surface, { detail: 1, clientX: 10, clientY: 10 });
      expect(onSelect).toHaveBeenLastCalledWith({ lane: 0, source: 3, entity: "t7" });
      drawn.renderer?.pick.mockReturnValue(null);
      fireEvent.click(surface, { detail: 1 });
      expect(onSelect).toHaveBeenLastCalledWith(null);
      fireEvent.click(surface, { detail: 2 });
      expect(onSelect).toHaveBeenCalledTimes(2);

      fireEvent.doubleClick(surface);
      expect(onViewport).toHaveBeenLastCalledWith(FIT);
    });

    it("reports the object under the pointer when it changes, and none once it leaves", () => {
      const { surface, onHover } = mount();
      drawn.renderer?.pick.mockReturnValue(picked("t7"));
      fireEvent.pointerMove(surface, { clientX: 5, clientY: 5 });
      fireEvent.pointerMove(surface, { clientX: 6, clientY: 5 });
      expect(onHover.mock.calls).toEqual([[picked("t7")]]);
      fireEvent.pointerLeave(surface);
      expect(onHover).toHaveBeenLastCalledWith(null);
    });

    it("drops the object on a zoom, and finds it again when the pointer moves or the next picture arrives", () => {
      let surface: Surface | null = null;
      const { surface: element, onHover } = mount({
        session: { setSurface: (next: Surface | null) => (surface = next) } as never,
      });
      drawn.renderer?.pick.mockReturnValue(picked("t7"));
      drawn.renderer?.zoomAt.mockReturnValue(zoomed);
      const redrawn = () => (drawn.renderer as unknown as { onDrawn: () => void }).onDrawn();
      fireEvent.pointerMove(element, { clientX: 5, clientY: 5 });
      fireEvent.wheel(element, { deltaY: -100, clientX: 5, clientY: 5 });
      expect(onHover).toHaveBeenLastCalledWith(null);
      // The zoom itself redraws; that does not bring the object back.
      redrawn();
      expect(onHover).toHaveBeenLastCalledWith(null);
      fireEvent.pointerMove(element, { clientX: 6, clientY: 5 });
      expect(onHover).toHaveBeenLastCalledWith(picked("t7"));

      fireEvent.wheel(element, { deltaY: -100, clientX: 5, clientY: 5 });
      expect(onHover).toHaveBeenLastCalledWith(null);
      act(() => surface?.present({ width: 4, height: 4 } as VideoFrame, []));
      redrawn();
      expect(onHover).toHaveBeenLastCalledWith(picked("t7"));
    });

    it("reports no object and selects nothing while object details are off", () => {
      const { surface, onHover, onSelect } = mount({ objectDetails: false });
      drawn.renderer?.pick.mockReturnValue(picked("t7"));
      fireEvent.pointerMove(surface, { clientX: 5, clientY: 5 });
      fireEvent.click(surface, { detail: 1 });
      expect(onHover).not.toHaveBeenCalled();
      expect(onSelect).not.toHaveBeenCalled();
    });
  });
});
