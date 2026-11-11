import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { SidebarView, View } from "../generated/api";
import { scenario, seatAccess } from "./fakeScenario";
import { FakeSocket, type Room } from "./fakeSession";
import {
  SessionProvider,
  useCan,
  useCommand,
  useCursors,
  useRenameWorkspace,
  useSession,
  useView,
} from "./session";

let renders = 0;
let frames: FrameRequestCallback[] = [];
const flushFrames = () => act(() => frames.splice(0).forEach((frame) => frame(0)));

function Probe() {
  const [sidebar, setSidebar] = useView("sidebar");
  const canView = useCan("view");
  const { follow } = useSession();
  renders += 1;
  return (
    <>
      <p>
        {sidebar.view} {sidebar.open ? "open" : "closed"} {canView ? "editable" : "read-only"}
      </p>
      <button onClick={() => setSidebar({ ...sidebar, view: "live" })}>Show live</button>
      <button onClick={() => setSidebar({ ...sidebar, open: false })}>Hide sidebar</button>
      <button onClick={() => void follow()}>Follow</button>
    </>
  );
}

function Renamer() {
  const { edit } = useRenameWorkspace();
  const command = useCommand();
  return (
    <>
      <button onClick={() => Array.from({ length: 30 }, (_, i) => edit(`Review ${i}`))}>
        Type
      </button>
      <button onClick={() => void command({ type: "follow" })}>Send</button>
    </>
  );
}

function CursorList() {
  return <p>cursors: {[...useCursors().keys()].join(",")}</p>;
}

const app = (
  <SessionProvider>
    <Probe />
    <Renamer />
    <CursorList />
  </SessionProvider>
);
const click = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
const view = (sidebar: SidebarView): View => ({
  sidebar: { view: sidebar, open: true },
  viewers: {},
});

/** Someone else's session, which this tab joined by its link. */
function watch(access: "watch" | "explore"): Room {
  const room = FakeSocket.host(access);
  room.presenter = view("bookmarks");
  window.sessionStorage.setItem(
    "ax-devil.credential",
    JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
  );
  render(app);
  return room;
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => frames.push(callback));
  vi.stubGlobal("cancelAnimationFrame", () => {});
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
  renders = 0;
  frames = [];
});

describe("session", () => {
  it("shows the owner's change at once and publishes only the latest one", async () => {
    render(app);
    await screen.findByText("workspace open editable");
    const room = FakeSocket.rooms.get("room-1")!;

    click("Show live");
    click("Hide sidebar");
    expect(screen.getByText("live closed editable")).toBeTruthy();
    expect(room.presenter.sidebar.open).toBe(true);
    flushFrames();
    expect(room.presenter.sidebar).toEqual({ view: "live", open: false });
    expect(FakeSocket.latest.received.filter((message) => message.type === "view")).toHaveLength(1);
  });

  it("shows a following Guest the presenter's view and lets nothing change it", async () => {
    const room = watch("watch");
    expect(await screen.findByText("bookmarks open read-only")).toBeTruthy();

    click("Show live");
    flushFrames();
    expect(screen.getByText("bookmarks open read-only")).toBeTruthy();
    expect(FakeSocket.latest.received.map((message) => message.type)).not.toContain("view");
    act(() => room.publish(view("plugins")));
    expect(screen.getByText("plugins open read-only")).toBeTruthy();
  });

  it("starts exploring from the presenter's view, and goes back with Follow", async () => {
    const room = watch("explore");
    await screen.findByText("bookmarks open editable");

    click("Hide sidebar");
    flushFrames();
    act(() => room.publish(view("plugins")));
    expect(screen.getByText("bookmarks closed editable")).toBeTruthy();
    expect(room.seats[1]?.view?.sidebar.open).toBe(false);

    click("Follow");
    expect(await screen.findByText("plugins open editable")).toBeTruthy();
    expect(room.seats[1]?.view).toBeNull();
  });

  it("stops showing a change the backend refused, once it has settled it", async () => {
    const room = watch("explore");
    await screen.findByText("bookmarks open editable");
    FakeSocket.latest.defer = true;
    click("Hide sidebar");
    flushFrames();

    // The owner stops Guests exploring before the backend gets to the change.
    act(() => room.allow("watch"));
    expect(screen.getByText("bookmarks closed read-only")).toBeTruthy();
    act(() => FakeSocket.latest.flush());
    expect(screen.getByText("bookmarks open read-only")).toBeTruthy();
  });

  it("holds a part that points at a Doc it has not got until the Doc arrives", async () => {
    render(app);
    await screen.findByText("workspace open editable");
    const socket = FakeSocket.latest;
    const part = { ...view("live"), active: undefined };
    const seat = {
      type: "seat" as const,
      rev: 9,
      docRev: 9,
      presenterRev: 9,
      access: "owner" as const,
      can: seatAccess().owner,
      mode: "following" as const,
      view: part,
      viewsDone: 0,
    };
    act(() => socket.emit(seat));
    expect(screen.getByText("workspace open editable")).toBeTruthy();
    act(() =>
      socket.emit({
        type: "doc",
        docRev: 9,
        doc: scenario("empty").doc(),
      }),
    );
    expect(screen.getByText("live open editable")).toBeTruthy();
  });

  it("takes the seat back after a drop, with the rotated token, and sends the change that was on its way again", async () => {
    const room = watch("explore");
    await screen.findByText("bookmarks open editable");
    const first = FakeSocket.latest;
    const token = room.seats[1]!.resume;
    // The change is on its way when the connection drops.
    first.defer = true;
    click("Hide sidebar");
    flushFrames();
    vi.useFakeTimers();

    act(() => first.drop());
    expect(screen.getByRole("status").textContent).toContain("Reconnecting");
    await act(() => vi.advanceTimersByTimeAsync(600));
    const second = FakeSocket.latest;
    expect(second.received[0]).toMatchObject({ type: "hello", resume: token });
    // Five echoed round trips to know the backend's clock, again on every connection.
    expect(second.received.filter((message) => message.type === "timeSync")).toHaveLength(5);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText("bookmarks closed editable")).toBeTruthy();

    flushFrames();
    const resent = second.received.filter((message) => message.type === "view");
    expect(resent).toHaveLength(1);
    expect(resent[0]).toMatchObject({ seq: 2 });
    expect(screen.getByText("bookmarks closed editable")).toBeTruthy();
    expect(room.seats[1]?.view?.sidebar.open).toBe(false);
  });

  it("does not retry a refused link", async () => {
    const room = watch("watch");
    await screen.findByText(/^bookmarks/);
    cleanup();
    window.sessionStorage.clear();
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: "wrong" }),
    );
    render(app);
    vi.useFakeTimers();
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(screen.getByRole("alert").textContent).toContain("not valid");
    expect(FakeSocket.sockets).toHaveLength(2);
  });

  it("tells the cursor store, not the shell, when a participant's cursor moves", async () => {
    const room = watch("watch");
    await screen.findByText(/^bookmarks/);
    act(() => room.publish(view("live")));
    const rendered = renders;
    const at = { region: "main", scope: "welcome", x: 0.5, y: 0.5 } as const;

    act(() => FakeSocket.latest.cursor(1, at));
    expect(screen.getByText("cursors: 1")).toBeTruthy();
    expect(renders).toBe(rendered);
    act(() => FakeSocket.latest.cursor(1, null));
    expect(screen.getByText("cursors:")).toBeTruthy();
  });

  it("sends a burst of renames as the first and the last, and the last before the next command", async () => {
    render(app);
    await screen.findByText("workspace open editable");
    vi.useFakeTimers();
    const commands = () =>
      FakeSocket.latest.received.flatMap((message) =>
        message.type === "command" ? [message.cmd] : [],
      );

    click("Type");
    expect(commands()).toHaveLength(1);
    act(() => void vi.advanceTimersByTime(1_000));
    expect(commands().map((cmd) => (cmd.type === "content" ? cmd.command : cmd))).toEqual([
      { type: "renameWorkspace", name: "Review 0", epoch: 0 },
      { type: "renameWorkspace", name: "Review 29", epoch: 0 },
    ]);

    click("Type");
    click("Send");
    expect(commands().slice(-2)).toEqual([
      expect.objectContaining({
        command: { type: "renameWorkspace", name: "Review 29", epoch: 0 },
      }),
      { type: "follow" },
    ]);
  });

  it("does not send a name typed for a workspace that was replaced", async () => {
    render(app);
    await screen.findByText("workspace open editable");
    vi.useFakeTimers();
    const renamed = () =>
      FakeSocket.latest.received.filter(
        (message) => message.type === "command" && message.cmd.type === "content",
      );

    click("Type");
    expect(renamed()).toHaveLength(1);
    act(() =>
      FakeSocket.latest.emit({
        type: "doc",
        docRev: 99,
        doc: scenario("empty").doc("kept"),
      }),
    );
    act(() => void vi.advanceTimersByTime(1_000));
    expect(renamed()).toHaveLength(1);
  });
});
