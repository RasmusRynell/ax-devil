import { act, render, screen } from "@testing-library/react";
import { useLayoutEffect } from "react";
import type { Access, ItemState } from "../generated/api";
import { entry, failed, FakeSocket, playlist, video, type Room } from "../session/fakeSession";
import { SessionProvider, useView } from "../session/session";
import { KeptStateProvider } from "./keptState";
import { RevealShown, useFolds } from "./RevealShown";
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
  orders: ["workspace", "name"],
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

/** Opens every row the window has just given its first fold, so a test sees the whole tree. */
export function OpenAll() {
  const [{ known }, setHeld] = useFolds("workspace");
  useLayoutEffect(() => setHeld((now) => ({ ...now, collapsed: new Set() })), [known]);
  return null;
}

/** The tree of the window's own Session, or with `access` of one someone else shares with that access. */
export async function renderTree(items: ItemState[] = ITEMS, hidden = HIDDEN, access?: Access) {
  const shared = access && FakeSocket.host(access);
  if (shared)
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: shared.id, secret: shared.secret }),
    );
  render(
    <SessionProvider>
      <KeptStateProvider>
        <RevealShown view="workspace" />
        <OpenAll />
        <WorkspaceTree view="workspace" {...LISTING} />
      </KeptStateProvider>
      <View />
    </SessionProvider>,
  );
  await act(async () => {});
  const room = shared ?? (FakeSocket.rooms.get("room-1") as Room);
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
