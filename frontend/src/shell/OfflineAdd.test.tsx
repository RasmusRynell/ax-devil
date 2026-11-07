import { afterEach, beforeEach, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { HandlersInfo } from "../generated/api";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { OfflineAdd } from "./OfflineAdd";

const HANDLERS: HandlersInfo = {
  decoders: [],
  resolvers: [
    {
      id: "folder_pair",
      display_name: "Folder Pair",
      description: "Pairs videos\n  with overlays.",
      fields: [],
    },
    { id: "mot_challenge", display_name: "MOT Challenge", description: "", fields: [] },
  ],
};

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => HANDLERS })),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

it("offers a single video, then the resolvers in order; a resolver opens Add Playlist with it chosen", async () => {
  const onRun = vi.fn();
  render(
    <SessionProvider>
      <OfflineAdd onRun={onRun} />
    </SessionProvider>,
  );
  await screen.findByRole("button", { name: "Folder Pair" });
  expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual([
    "Single video",
    "Folder Pair",
    "MOT Challenge",
  ]);
  expect(screen.getByRole("button", { name: "Folder Pair" }).title).toBe(
    "Pairs videos with overlays.",
  );

  fireEvent.click(screen.getByRole("button", { name: "Single video" }));
  expect(onRun).toHaveBeenCalledWith("add-video");

  fireEvent.click(screen.getByRole("button", { name: "MOT Challenge" }));
  const source = (await screen.findByLabelText("Playlist source")) as HTMLSelectElement;
  expect(source.value).toBe("mot_challenge");
});
