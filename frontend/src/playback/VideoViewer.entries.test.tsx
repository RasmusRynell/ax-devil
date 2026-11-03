import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vite-plus/test";
import { FakeSocket, video } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { SessionStore } from "../session/store";
import type { ClientMessage } from "./protocol";
import {
  Viewer,
  flushFrames,
  installViewerHarness,
  setSpeed,
  slider,
  sockets,
  speed,
} from "./videoViewerHarness";

// The picture needs WebGL; this suite is about the session behind it.
vi.mock("./VideoCanvas", () => ({ VideoCanvas: () => null }));

installViewerHarness();

describe("video viewer entries", () => {
  const seed = (room: ReturnType<typeof FakeSocket.host>, entry: string, frame: number) => {
    room.presenter = {
      ...room.presenter,
      viewers: {
        a: { entry, clock: { frame, playing: false, speedCenti: 100, atUs: 0 } },
      },
    };
  };
  const tickets = () =>
    FakeSocket.sockets
      .flatMap((socket) => socket.received)
      .flatMap((m) => (m.type === "command" && m.cmd.type === "requestTicket" ? [m.cmd.key] : []));
  const mount = async (sent: ClientMessage[], entryId?: string) => {
    render(
      <SessionProvider>
        <Viewer sent={sent} entryId={entryId} />
      </SessionProvider>,
    );
    await act(async () => {});
    return FakeSocket.rooms.get("room-1")!;
  };

  it("asks for the entry it plays, and starts an entry at its first frame once the view names it", async () => {
    const sent: ClientMessage[] = [];
    const room = await mount(sent);
    seed(room, "content-a", 37);
    act(() => room.setItems([video("a")]));
    expect((await slider()).value).toBe("37");
    expect(tickets().at(-1)).toMatchObject({ content: "content-a", entry: "content-a" });

    // The playlist viewer shows another entry: its position is its own, and none of entry A's goes into it.
    sent.length = 0;
    act(() => room.publish({ ...room.presenter, viewers: { a: { entry: "content-b" } } }));
    await act(async () => {});
    expect((await slider()).value).toBe("0");
    expect(sent.filter((message) => message.type === "Open")).toEqual([
      { type: "Open", ticket: expect.any(String), data_bytes: expect.any(Number) },
    ]);
    expect(tickets().at(-1)).toMatchObject({ entry: "content-b" });
    flushFrames();
    expect(room.presenter.viewers.a).toEqual({ entry: "content-b" });
  });

  it("reopens an entry where it was left when the view comes back to it, and a first visit starts at its first frame", async () => {
    const sent: ClientMessage[] = [];
    const room = await mount(sent);
    act(() => room.setItems([video("a")]));
    fireEvent.change(await slider(), { target: { value: "37" } });
    setSpeed("2");
    flushFrames();

    act(() => room.publish({ ...room.presenter, viewers: { a: { entry: "content-b" } } }));
    await act(async () => {});
    expect((await slider()).value).toBe("0");
    // The speed belongs to the viewer: the last one set plays on in whichever entry opens next.
    expect(speed().textContent).toBe("2×");
    fireEvent.change(await slider(), { target: { value: "5" } });
    setSpeed("3");
    flushFrames();

    // Choosing an entry again leaves the view without a clock for it; the viewer remembers its own.
    sent.length = 0;
    act(() => room.publish({ ...room.presenter, viewers: { a: { entry: "content-a" } } }));
    await act(async () => {});
    expect((await slider()).value).toBe("37");
    expect(speed().textContent).toBe("3×");
    expect(sent.find((message) => message.type === "Open")).toMatchObject({ start_frame: 37 });
    act(() => room.publish({ ...room.presenter, viewers: { a: { entry: "content-b" } } }));
    await act(async () => {});
    expect((await slider()).value).toBe("5");
  });

  it("keeps no position for a window that follows, which takes the presenter's clock", async () => {
    const room = FakeSocket.host("watch");
    room.setItems([video("a")]);
    seed(room, "content-a", 12);
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    render(
      <SessionProvider store={new SessionStore({ now: () => 10_000 })}>
        <Viewer sent={[]} />
      </SessionProvider>,
    );
    expect((await slider()).value).toBe("12");
    act(() => room.publish({ ...room.presenter, viewers: { a: { entry: "content-b" } } }));
    await act(async () => {});
    seed(room, "content-a", 60);
    act(() => room.publish({ ...room.presenter }));
    await act(async () => {});
    expect((await slider()).value).toBe("60");
  });

  it("opens an entry at its first frame once the window stops following, as it kept nothing while it did", async () => {
    const room = FakeSocket.host("explore");
    room.setItems([video("a")]);
    seed(room, "content-a", 12);
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    const sent: ClientMessage[] = [];
    const store = new SessionStore({ now: () => 10_000 });
    const tree = (entryId?: string) => (
      <SessionProvider store={store}>
        <Viewer sent={sent} entryId={entryId} />
      </SessionProvider>
    );
    const view = render(tree());
    expect((await slider()).value).toBe("12");
    act(() => room.publish({ ...room.presenter, viewers: { a: { entry: "content-b" } } }));
    await act(async () => {});
    // A seek makes the seat explore: the window follows no more.
    fireEvent.change(await slider(), { target: { value: "5" } });
    flushFrames();
    expect(room.seats[1]?.view).toBeTruthy();

    sent.length = 0;
    view.rerender(tree("content-a"));
    await act(async () => {});
    expect((await slider()).value).toBe("0");
    expect(sent.find((message) => message.type === "Open")).toEqual({
      type: "Open",
      ticket: expect.any(String),
      data_bytes: expect.any(Number),
    });
  });

  it("does not resume a position that was kept for another entry", async () => {
    const sent: ClientMessage[] = [];
    const room = await mount(sent, "content-a");
    // The view holds a clock for entry B while the viewer shows A: it is B's, not A's.
    seed(room, "content-b", 55);
    act(() => room.setItems([video("a")]));
    expect((await slider()).value).toBe("0");
    expect(sent[0]).toEqual({
      type: "Open",
      ticket: expect.any(String),
      data_bytes: expect.any(Number),
    });
  });

  it("moves a following window to the presenter's new entry without a revoked notice", async () => {
    const room = FakeSocket.host("watch");
    room.setItems([video("a")]);
    seed(room, "content-a", 12);
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    render(
      <SessionProvider store={new SessionStore({ now: () => 10_000 })}>
        <Viewer sent={[]} />
      </SessionProvider>,
    );
    expect((await slider()).value).toBe("12");

    act(() => room.publish({ ...room.presenter, viewers: { a: { entry: "content-b" } } }));
    await act(async () => {});
    // The backend ends the stream of the entry the presenter left; that is not news to this window.
    act(() => sockets[0]({ type: "closed", code: 4403 }));
    expect((await slider()).value).toBe("0");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(tickets().at(-1)).toMatchObject({ entry: "content-b" });
  });
});
