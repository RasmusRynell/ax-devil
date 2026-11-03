import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps, ReactNode } from "react";
import { describe, expect, it, vi } from "vite-plus/test";
import { installFakeFullscreen } from "../fakeFullscreen";
import { DEFAULT_SETTINGS } from "../fakeSettings";
import { FakeSocket, video } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { NO_ACTIONS, ViewerActionsContext, type ViewerActions } from "../viewer/actions";
import type { Picked } from "./contract";
import type { ClientMessage, LaneInfo } from "./protocol";
import type { Geometry } from "./renderer";
import type { VideoCanvas } from "./VideoCanvas";
import { Viewer, installViewerHarness, slider } from "./videoViewerHarness";
import { FIT, ZOOM_STEP } from "./viewport";

// What the canvas was last given; the picture needs WebGL, and these tests are about what the viewer does with it.
const canvas = vi.hoisted(() => ({
  props: undefined as ComponentProps<typeof VideoCanvas> | undefined,
}));
vi.mock("./VideoCanvas", () => ({
  VideoCanvas: (props: ComponentProps<typeof VideoCanvas>) => {
    canvas.props = props;
    return null;
  },
}));

installViewerHarness();
const fullscreen = installFakeFullscreen();

const SELECTION = { lane: 0, source: 0, entity: "7" };
const PICKED: Picked = {
  lane: 0,
  source: 0,
  sample: 0,
  row: 0,
  entity: "7",
  label: "7 car",
  detail: "score 0.82",
  box: { x: 10, y: 20, width: 30, height: 40 },
};
const OFF = {
  ...DEFAULT_SETTINGS,
  values: { ...DEFAULT_SETTINGS.values, "overlays.object_details": false },
};

const registry = { current: NO_ACTIONS };
const offer = (next: ViewerActions | ((now: ViewerActions) => ViewerActions)) => {
  registry.current = typeof next === "function" ? next(registry.current) : next;
};

const tree = (viewer: ReactNode) => (
  <SessionProvider>
    <ViewerActionsContext.Provider value={offer}>{viewer}</ViewerActionsContext.Provider>
  </SessionProvider>
);

/** The viewer of an open video; `show` renders it again with other settings or another entry. */
async function mount(settings?: typeof OFF, lanes?: LaneInfo[]) {
  registry.current = NO_ACTIONS;
  // One array for the life of the viewer: another would give it a new connection and restart playback.
  const sent: ClientMessage[] = [];
  const view = render(tree(<Viewer sent={sent} settings={settings} lanes={lanes} />));
  await act(async () => {});
  act(() => FakeSocket.rooms.get("room-1")?.setItems([video("a")]));
  await slider();
  const props = () => canvas.props as ComponentProps<typeof VideoCanvas>;
  return {
    props,
    sent,
    show: (next: Partial<Parameters<typeof Viewer>[0]>) =>
      view.rerender(tree(<Viewer sent={sent} {...next} />)),
    run: (id: string) => act(() => registry.current[id]?.()),
  };
}

/** A geometry that zooms by the factor and reports `outlines`. */
const geometry = (outlines: ReturnType<Geometry["boxes"]> = []) =>
  ({
    zoomAt: vi.fn((viewport, factor) => ({ ...viewport, zoom: viewport.zoom * factor })),
    boxes: vi.fn(() => outlines),
  }) as unknown as Geometry & { boxes: ReturnType<typeof vi.fn> };

const inspector = () => screen.queryByRole("complementary", { name: "Inspector" });

describe("video viewer objects", () => {
  it("opens the inspector for a click on an object and closes it by empty space, Escape or its button", async () => {
    const { props } = await mount();
    expect(inspector()).toBeNull();
    act(() => props().onSelect?.(SELECTION));
    expect(inspector()).toBeTruthy();
    act(() => props().onSelect?.(null));
    expect(inspector()).toBeNull();
    act(() => props().onSelect?.(SELECTION));
    fireEvent.keyDown(inspector() as HTMLElement, { key: "Escape" });
    expect(inspector()).toBeNull();
    act(() => props().onSelect?.(SELECTION));
    fireEvent.click(screen.getByRole("button", { name: "Close inspector" }));
    expect(inspector()).toBeNull();
  });

  it("shows one lane alone in fullscreen while the others keep playing, and Escape only leaves it", async () => {
    const lanes: LaneInfo[] = [
      { label: "DPM", slot: "DPM", source: 0 },
      { label: "GT", slot: "GT", source: 1 },
    ];
    const { props, run, sent } = await mount(undefined, lanes);
    expect(props().cells?.map(({ lane }) => lane)).toEqual([0, 1]);

    run("lane-fullscreen");
    expect(props().cells?.map(({ lane }) => lane)).toEqual([0]);
    expect(sent.some((message) => "subscribed" in message && !message.subscribed)).toBe(false);
    expect(sent.some((message) => "subscribed" in message)).toBe(true);

    act(() => props().onSelect?.(SELECTION));
    fireEvent.keyDown(inspector() as HTMLElement, { key: "Escape" });
    // The Escape that leaves fullscreen does not also clear the selection.
    expect(fullscreen.exit).toHaveBeenCalledOnce();
    expect(inspector()).toBeTruthy();
    expect(props().cells?.map(({ lane }) => lane)).toEqual([0, 1]);
    fireEvent.keyDown(inspector() as HTMLElement, { key: "Escape" });
    expect(inspector()).toBeNull();
  });

  it("keeps the selection when Escape closes a lane menu", async () => {
    const lanes: LaneInfo[] = [
      { label: "DPM", slot: "DPM", source: null },
      { label: "GT", slot: "GT", source: null },
    ];
    const { props } = await mount(undefined, lanes);
    act(() => props().onSelect?.(SELECTION));
    fireEvent.contextMenu(screen.getByText("DPM"));
    fireEvent.keyDown(screen.getByRole("menuitem", { name: /Export Video/ }), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(inspector()).toBeTruthy();
  });

  it("opens and closes the media tools by their command or their button", async () => {
    const { run } = await mount();
    const tools = () => screen.queryByRole("complementary", { name: "Media tools" });
    expect(tools()).toBeNull();
    run("media-tools");
    expect(tools()).toBeTruthy();
    run("media-tools");
    expect(tools()).toBeNull();
    run("media-tools");
    fireEvent.click(screen.getByRole("button", { name: "Close media tools" }));
    expect(tools()).toBeNull();
  });

  it("shows the hover chip of the object under the pointer", async () => {
    const { props } = await mount();
    expect(props().objectDetails).toBe(true);
    act(() => props().onHover?.(PICKED));
    expect(screen.getByRole("tooltip").textContent).toBe("7 carscore 0.82");
    act(() => props().onHover?.(null));
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("shows no chip, outline or inspector while object details are off, and forgets the selection", async () => {
    const { props, show } = await mount();
    act(() => props().onSelect?.(SELECTION));
    show({ settings: OFF });
    expect(props().objectDetails).toBe(false);
    expect(inspector()).toBeNull();
    const shown = geometry([{ lane: 0, rect: PICKED.box, kind: "selected" }]);
    act(() => {
      props().onDrawn?.(shown);
      props().onHover?.(PICKED);
    });
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(document.querySelector("[data-outline]")).toBeNull();
    show({});
    expect(inspector()).toBeNull();
  });

  it("places outlines where the pictures' geometry puts them", async () => {
    const { props } = await mount();
    const rect = { x: 5, y: 6, width: 7, height: 8 };
    const shown = geometry([{ lane: 0, rect, kind: "selected" }]);
    act(() => props().onDrawn?.(shown));
    act(() => props().onSelect?.(SELECTION));
    expect(shown.boxes).toHaveBeenLastCalledWith(SELECTION, null);
    const outline = document.querySelector<HTMLElement>("[data-outline=selected]");
    expect([outline?.style.left, outline?.style.top]).toEqual(["5px", "6px"]);
    expect([outline?.style.width, outline?.style.height]).toEqual(["7px", "8px"]);
    act(() => props().onHover?.(PICKED));
    expect(shown.boxes).toHaveBeenLastCalledWith(SELECTION, PICKED);
  });

  it("zooms toward the first cell by a step, zooms back and resets, and keeps the zoom across entries", async () => {
    const { props, run, show } = await mount();
    act(() => props().onDrawn?.(geometry()));
    expect(props().viewport).toEqual(FIT);
    run("zoom-in");
    run("zoom-in");
    expect(props().viewport?.zoom).toBeCloseTo(ZOOM_STEP ** 2);
    run("zoom-out");
    expect(props().viewport?.zoom).toBeCloseTo(ZOOM_STEP);
    show({ entryId: "content-b" });
    await slider();
    expect(props().viewport?.zoom).toBeCloseTo(ZOOM_STEP);
    run("zoom-reset");
    expect(props().viewport).toEqual(FIT);
  });
});
