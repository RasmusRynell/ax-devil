import { afterEach, beforeEach, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { entry, failed, FakeSocket, playlist, video, type Room } from "../session/fakeSession";
import { FakeSettings } from "../fakeSettings";
import { SessionProvider } from "../session/session";
import { KeptStateProvider } from "./keptState";
import { Sidebar } from "./Sidebar";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

it("lists only Videos and Playlists in Offline, and counts them in the header", async () => {
  render(
    <SessionProvider>
      <KeptStateProvider>
        <Sidebar view="offline" onRun={() => {}} onAddLive={() => {}} />
      </KeptStateProvider>
    </SessionProvider>,
  );
  await act(async () => {});
  const room = FakeSocket.rooms.get("room-1") as Room;
  act(() =>
    room.setItems([
      { ...failed("cam"), kind: "live" },
      video("v", false),
      playlist("mot", [entry("mot/A", "A", ["GT"])], false),
    ]),
  );
  expect(screen.getByRole("heading", { name: /^Videos/ })).toBeTruthy();
  expect(screen.getByRole("heading", { name: /^Playlists/ })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: /^Live/ })).toBeNull();
  expect(screen.getByText("2")).toBeTruthy();
  expect(screen.getByRole("searchbox", { name: "Search files" })).toBeTruthy();
});

it("shows the start panel alone while the workspace is empty, and the list once it has an item", async () => {
  render(
    <FakeSettings>
      <SessionProvider>
        <KeptStateProvider>
          <Sidebar view="workspace" onRun={() => {}} onAddLive={() => {}} />
        </KeptStateProvider>
      </SessionProvider>
    </FakeSettings>,
  );
  await act(async () => {});
  expect(screen.getByRole("button", { name: "Open Workspace" }).title).toBe(
    "Open Workspace (Ctrl+O)",
  );
  expect(screen.queryByRole("searchbox")).toBeNull();
  act(() => (FakeSocket.rooms.get("room-1") as Room).setItems([video("v", false)]));
  expect(screen.getByRole("searchbox", { name: "Search items" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open Workspace" })).toBeNull();
});

it("offers the Location order in Offline and not in Workspace", async () => {
  const sorts = async (view: "offline" | "workspace") => {
    render(
      <SessionProvider>
        <KeptStateProvider>
          <Sidebar view={view} onRun={() => {}} onAddLive={() => {}} />
        </KeptStateProvider>
      </SessionProvider>,
    );
    await act(async () => {});
    act(() => (FakeSocket.rooms.get("room-1") as Room).setItems([video("v", false)]));
    fireEvent.click(screen.getByRole("button", { name: "Sort" }));
    const names = screen.getAllByRole("menuitemcheckbox").map((item) => item.textContent);
    cleanup();
    return names;
  };
  expect(await sorts("offline")).toEqual(["Workspace order", "Name", "Location"]);
  expect(await sorts("workspace")).toEqual(["Workspace order", "Name"]);
});

it("keeps a view's search, sort and folds while another view or no view is shown", async () => {
  const page = (view: "workspace" | "offline" | null) => (
    <SessionProvider>
      <KeptStateProvider>
        {view && <Sidebar view={view} onRun={() => {}} onAddLive={() => {}} />}
      </KeptStateProvider>
    </SessionProvider>
  );
  const { rerender } = render(page("offline"));
  await act(async () => {});
  const room = FakeSocket.rooms.get("room-1") as Room;
  act(() => room.setItems([video("v", false), video("w", false)]));
  fireEvent.click(screen.getByRole("button", { name: "Videos" }));
  expect(screen.queryByRole("tree")).toBeNull();
  const search = () => screen.getByRole("searchbox", { name: "Search files" }) as HTMLInputElement;
  fireEvent.change(search(), { target: { value: "w" } });
  fireEvent.click(screen.getByRole("button", { name: "Sort" }));
  fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "Name" }));

  rerender(page("workspace"));
  expect(screen.getByRole("tree", { name: "Videos" })).toBeTruthy();
  rerender(page(null));
  rerender(page("offline"));
  expect(search().value).toBe("w");
  fireEvent.click(screen.getByRole("button", { name: "Sort" }));
  expect(screen.getByRole("menuitemcheckbox", { name: "Name" }).getAttribute("aria-checked")).toBe(
    "true",
  );
  fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
  fireEvent.change(search(), { target: { value: "" } });
  expect(screen.queryByRole("tree")).toBeNull();
});

it("lets a seat that edits content rename the workspace from its title, and no other", async () => {
  const sidebar = (
    <FakeSettings>
      <SessionProvider>
        <KeptStateProvider>
          <Sidebar view="workspace" onRun={() => {}} onAddLive={() => {}} />
        </KeptStateProvider>
      </SessionProvider>
    </FakeSettings>
  );
  const { unmount } = render(sidebar);
  await act(async () => {});
  expect(screen.getByRole("button", { name: "Untitled" })).toBeTruthy();
  unmount();

  const room = FakeSocket.host("watch");
  window.sessionStorage.setItem(
    "ax-devil.credential",
    JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
  );
  render(sidebar);
  await act(async () => {});
  expect(screen.getByRole("heading", { name: "Untitled" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Untitled" })).toBeNull();
});
