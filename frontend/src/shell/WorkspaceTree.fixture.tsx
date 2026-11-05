import { act, render, screen } from "@testing-library/react";
import type { ItemState } from "../generated/api";
import { entry, failed, FakeSocket, playlist, video, type Room } from "../session/fakeSession";
import { SessionProvider, useView } from "../session/session";
import { WorkspaceTree } from "./WorkspaceTree";

export const MOT = playlist(
  "mot",
  [entry("mot/A", "A", ["GT", "DPM"]), entry("mot/B", "B", ["GT"])],
  false,
);
// A video whose entry has no lanes, so its row has no children.
export const CLIP: ItemState = {
  ...video("v", false),
  resolution: {
    status: "Resolved",
    content: {
      kind: "SeekableVideo",
      id: "content-v",
      generation: 1,
      display_name: "v.mp4",
      entry: entry("content-v", "v.mp4", []),
    },
  },
};
/** What the Workspace view lists. */
export const LISTING = {
  listed: ["live", "videos", "playlists", "other"],
  search: "Search items",
  empty: "No items",
} as const;
export const ITEMS = [MOT, CLIP, failed("bad", "File not found")];
export const HIDDEN = [{ entry: "mot/A", lane: "DPM" }, { entry: "mot/B" }];

/** What the window shows, as the tree changed it. */
function View() {
  const [active] = useView("active");
  const [viewers] = useView("viewers");
  return <div data-testid="view">{JSON.stringify({ active, viewers })}</div>;
}

export async function renderTree(items: ItemState[] = ITEMS, hidden = HIDDEN) {
  render(
    <SessionProvider>
      <WorkspaceTree {...LISTING} />
      <View />
    </SessionProvider>,
  );
  await act(async () => {});
  const room = FakeSocket.rooms.get("room-1") as Room;
  room.hidden = hidden;
  act(() => room.setItems(items));
  return room;
}

export const labels = () => screen.queryAllByRole("treeitem").map((item) => item.textContent);
export const shown = () => JSON.parse(screen.getByTestId("view").textContent ?? "{}");
export const commands = () =>
  FakeSocket.latest.received.flatMap((message) =>
    message.type === "command" && message.cmd.type === "content" ? [message.cmd.command] : [],
  );
