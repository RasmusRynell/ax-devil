import { act, render, screen } from "@testing-library/react";
import { useLayoutEffect } from "react";
import type { Access } from "../generated/api";
import { scenario, type Scenario } from "../session/fakeScenario";
import { FakeSocket, type Room } from "../session/fakeSession";
import { SessionProvider, useView } from "../session/session";
import { KeptStateProvider } from "../kit/keptState";
import { RevealShown, useFolds } from "./RevealShown";
import { WorkspaceTree } from "./WorkspaceTree";

/** A closed playlist with a hidden lane and a hidden entry, a video without lanes and a failed video. */
export const TREE = scenario("tree");
/** One of each kind of item: a playlist, a video, a failed, a resolving and an unreadable item, live streams. */
export const KINDS = scenario("tree-kinds");
/** What the Workspace view lists. */
export const LISTING = {
  listed: ["live", "videos", "playlists", "other"],
  orders: ["workspace", "name"],
  search: "Search items",
  empty: "No items",
} as const;

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

/** The tree of the window's own Session, or with `access` of one someone else shares with that access, showing `state` of `s`. */
export async function renderTree(s: Scenario = TREE, state = "initial", access?: Access) {
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
  act(() => room.show(s, state));
  return room;
}

export const labels = () => screen.queryAllByRole("treeitem").map((item) => item.textContent);
export const shown = () => JSON.parse(screen.getByTestId("view").textContent ?? "{}");
export const commands = () =>
  FakeSocket.latest.received.flatMap((message) =>
    message.type === "command" && message.cmd.type === "content" ? [message.cmd.command] : [],
  );
