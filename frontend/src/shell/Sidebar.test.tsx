import { afterEach, beforeEach, expect, it, vi } from "vite-plus/test";
import { act, cleanup, render, screen } from "@testing-library/react";
import { entry, failed, FakeSocket, playlist, video, type Room } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
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
      <Sidebar view="offline" onRun={() => {}} onAddLive={() => {}} />
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
