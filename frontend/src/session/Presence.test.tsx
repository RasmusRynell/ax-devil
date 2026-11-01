import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { CursorPoint } from "../generated/api";
import { FakeSocket } from "./fakeBackend";
import { Presence } from "./Presence";
import { SessionProvider, useSession } from "./session";

/** Where the region and the one element in it sit in this window; jsdom does no layout. */
const BOXES: Record<string, [left: number, top: number, width: number, height: number]> = {
  main: [100, 20, 200, 100],
  box: [150, 40, 50, 20],
};
let frames: FrameRequestCallback[] = [];

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => frames.push(callback));
  vi.stubGlobal("cancelAnimationFrame", () => (frames = []));
  const rect = (element: Element) => {
    const { cursorId = "", cursorRegion = "" } = (element as HTMLElement).dataset;
    const [left, top, width, height] = BOXES[cursorId || cursorRegion] ?? [0, 0, 0, 0];
    return new DOMRect(left, top, width, height);
  };
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    return rect(this);
  });
  document.elementFromPoint = (x, y) =>
    [...document.querySelectorAll("*")]
      .filter((element) => {
        const { left, top, right, bottom } = rect(element);
        return x >= left && x < right && y >= top && y < bottom;
      })
      .pop() ?? null;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
  frames = [];
});

function JoinNow({ link }: { link: string }) {
  const { joinLink } = useSession();
  return <button onClick={() => joinLink(link)}>Join</button>;
}

/** A watcher in someone else's session, with a region holding one element. */
async function watching() {
  const room = FakeSocket.host();
  render(
    <SessionProvider>
      <div data-cursor-region="main">
        <p data-cursor-id="box">Main</p>
      </div>
      <Presence scopes={{ main: "welcome" }} />
      <JoinNow link={room.link} />
    </SessionProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Join" }));
  await screen.findByText("Main");
  await act(() => Promise.resolve());
  return FakeSocket.latest;
}

const cursorAt = (at: CursorPoint | null) => ({ type: "cursor" as const, participant: 1, at });
const tag = () => screen.queryByText("Guest 1");

describe("presence", () => {
  it("sends the pointer once per frame, inside its element, and that it left", async () => {
    const socket = await watching();
    const main = screen.getByText("Main");
    const sent = () => socket.received.filter((message) => message.type === "cursor");

    fireEvent.pointerMove(main, { clientX: 155, clientY: 42 });
    fireEvent.pointerMove(main, { clientX: 160, clientY: 45 });
    act(() => frames.splice(0).forEach((frame) => frame(0)));
    expect(sent()).toEqual([
      {
        type: "cursor",
        at: {
          region: "main",
          scope: "welcome",
          x: 0.3,
          y: 0.25,
          element: { id: "box", x: 0.2, y: 0.25 },
        },
      },
    ]);

    fireEvent.pointerOut(main, { relatedTarget: null });
    expect(sent().at(-1)).toEqual({ type: "cursor", at: null });
  });

  it("draws a remote cursor inside the same element, and hides it where that is not possible here", async () => {
    const socket = await watching();
    const point = (rest: Partial<CursorPoint>): CursorPoint => ({
      region: "main",
      scope: "welcome",
      x: 0.5,
      y: 0.5,
      ...rest,
    });

    act(() => socket.emit(cursorAt(point({ element: { id: "box", x: 0.5, y: 0.5 } }))));
    expect(tag()?.parentElement?.style.left).toBe("175px");
    expect(tag()?.parentElement?.style.top).toBe("50px");

    act(() => socket.emit(cursorAt(point({ element: { id: "gone", x: 0.5, y: 0.5 } }))));
    expect(tag()).toBeNull();
    act(() => socket.emit(cursorAt(point({ scope: "live" }))));
    expect(tag()).toBeNull();
    act(() => socket.emit(cursorAt(point({}))));
    expect(tag()?.parentElement?.style.left).toBe("200px");
    act(() => socket.emit(cursorAt(null)));
    expect(tag()).toBeNull();
  });
});
