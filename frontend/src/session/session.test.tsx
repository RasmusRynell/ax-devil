import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ViewState } from "../generated/api";
import { FakeSocket, type Room } from "./fakeBackend";
import { SessionProvider, useCursors, useSession } from "./session";
import { useViewState } from "./view";

let renders = 0;
let link = "";

function Probe() {
  const { state: role, create, joinLink, follow } = useSession();
  const { state, set, editable } = useViewState();
  renders += 1;
  return (
    <>
      <p>
        {role} {state.sidebarView} {state.sidebarOpen ? "open" : "closed"}{" "}
        {editable ? "editable" : "read-only"}
      </p>
      <button onClick={() => set({ ...state, sidebarView: "live" })}>Show live</button>
      <button onClick={() => set({ ...state, sidebarView: "plugins" })}>Show plugins</button>
      <button onClick={() => set({ ...state, sidebarOpen: false })}>Hide sidebar</button>
      <button onClick={create}>Create</button>
      <button onClick={() => joinLink(link)}>Join</button>
      <button onClick={follow}>Follow</button>
    </>
  );
}

function CursorList() {
  return <p>cursors: {[...useCursors().keys()].join(",")}</p>;
}

const app = (
  <SessionProvider>
    <Probe />
    <CursorList />
  </SessionProvider>
);
const click = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
const view = (sidebarView: ViewState["sidebarView"]): ViewState => ({
  sidebarView,
  sidebarOpen: true,
});

/** Someone else's session, which this client watches. */
function watch(explore: boolean): Room {
  const room = FakeSocket.host();
  room.explore = explore;
  room.presenter = view("bookmarks");
  link = room.link;
  render(app);
  click("Join");
  return room;
}

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
  renders = 0;
});

describe("session", () => {
  it("is private until a session is created, and then publishes the owner's window", async () => {
    render(app);
    click("Show live");
    expect(screen.getByText("none live open editable")).toBeTruthy();
    expect(FakeSocket.sockets).toHaveLength(0);

    click("Create");
    expect(await screen.findByText("owner live open editable")).toBeTruthy();
    expect(FakeSocket.rooms.get("room-1")?.presenter).toEqual(view("live"));
    click("Hide sidebar");
    expect(FakeSocket.rooms.get("room-1")?.presenter.sidebarOpen).toBe(false);
  });

  it("shows a following watcher the presenter's view and lets nothing change it", async () => {
    const room = watch(false);
    expect(await screen.findByText("watcher bookmarks open read-only")).toBeTruthy();

    click("Show live");
    expect(screen.getByText("watcher bookmarks open read-only")).toBeTruthy();
    expect(FakeSocket.latest.received.map((message) => message.type)).toEqual(["hello"]);
    act(() => room.publish(view("plugins")));
    expect(screen.getByText("watcher plugins open read-only")).toBeTruthy();
  });

  it("starts exploring from the presenter's view, goes back with Follow, and is moved back when exploring is switched off", async () => {
    const room = watch(true);
    await screen.findByText("watcher bookmarks open editable");

    click("Hide sidebar");
    expect(screen.getByText("watcher bookmarks closed editable")).toBeTruthy();
    act(() => room.publish(view("plugins")));
    expect(screen.getByText("watcher bookmarks closed editable")).toBeTruthy();
    expect(room.presenter.sidebarOpen).toBe(true);

    click("Follow");
    expect(screen.getByText("watcher plugins open editable")).toBeTruthy();
    click("Show live");
    expect(screen.getByText("watcher live open editable")).toBeTruthy();
    act(() => room.allowExplore(false));
    expect(screen.getByText("watcher plugins open read-only")).toBeTruthy();
  });

  it("keeps a newer local edit over a snapshot that was sent before the backend saw it", async () => {
    const room = watch(true);
    await screen.findByText("watcher bookmarks open editable");
    click("Show live");
    FakeSocket.latest.defer = true;
    click("Hide sidebar");
    expect(screen.getByText("watcher live closed editable")).toBeTruthy();

    act(() => room.publish(view("plugins")));
    expect(screen.getByText("watcher live closed editable")).toBeTruthy();
    act(() => FakeSocket.latest.flush());
    expect(screen.getByText("watcher live closed editable")).toBeTruthy();
    expect(room.seats[1]?.view).toEqual({ sidebarView: "live", sidebarOpen: false });
  });

  it("keeps editing after a reload that took the seat back, whatever it edited before", async () => {
    const room = watch(true);
    await screen.findByText("watcher bookmarks open editable");
    click("Show live");
    click("Hide sidebar");

    cleanup();
    render(app);
    expect(await screen.findByText("watcher live closed editable")).toBeTruthy();

    click("Show plugins");
    expect(screen.getByText("watcher plugins closed editable")).toBeTruthy();
    expect(room.seats[1]?.view).toEqual({ sidebarView: "plugins", sidebarOpen: false });
  });

  it("joins the session a link names, not the one this tab was seated in", async () => {
    const first = watch(false);
    await screen.findByText(/^watcher/);
    const second = FakeSocket.host();
    cleanup();
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: second.id, secret: second.secret }),
    );

    render(app);
    await screen.findByText(/^watcher/);
    expect(second.seats).toHaveLength(2);
    expect(first.seats[1]?.socket).toBeNull();
    expect(FakeSocket.latest.received[0]).toMatchObject({ access: { type: "join" } });
  });

  it("does not retry a refused link, and resumes with the rotated token after a drop", async () => {
    const room = watch(false);
    await screen.findByText(/^watcher/);
    const token = room.seats[1]!.resume;
    vi.useFakeTimers();

    act(() => FakeSocket.latest.drop());
    expect(screen.getByRole("status").textContent).toContain("Reconnecting");
    await act(() => vi.advanceTimersByTimeAsync(600));
    expect(FakeSocket.latest.received[0]).toMatchObject({ access: { type: "resume", token } });
    expect(screen.getByText(/^watcher/)).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();

    link = `${room.id}.wrong`;
    click("Join");
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(screen.getByRole("alert").textContent).toContain("not valid");
    expect(FakeSocket.sockets).toHaveLength(3);
  });

  it("tells the cursor store, not the shell, when a participant's cursor moves", async () => {
    const room = watch(false);
    await screen.findByText(/^watcher/);
    act(() => room.publish(view("live")));
    const rendered = renders;
    const at = { region: "main", scope: "welcome", x: 0.5, y: 0.5 } as const;

    act(() => FakeSocket.latest.cursor(1, at));
    expect(screen.getByText("cursors: 1")).toBeTruthy();
    expect(renders).toBe(rendered);
    act(() => FakeSocket.latest.cursor(1, null));
    expect(screen.getByText("cursors:")).toBeTruthy();
  });
});
