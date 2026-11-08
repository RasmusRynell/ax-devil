import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { PlaybackSession } from "../playback/session";
import type { PlaybackView } from "../playback/view";
import { useCan, useCommand, useDoc, useFollowing, useSetView, useView } from "../session/session";
import { PaneActiveContext } from "../viewer/actions";

/** A frame to show, paused, once the viewer of an entry is ready. */
interface Request {
  /** The entry's id, as its viewer is given it. */
  entry: string;
  frame: number;
}

interface Seeking {
  request: Request | null;
  ask: (request: Request) => void;
  done: () => void;
}

const SeekContext = createContext<Seeking | null>(null);

/** Holds the frame the Bookmarks view asked a viewer to show until that viewer is ready for it. */
export function SeekProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<Request | null>(null);
  const value = useMemo(
    () => ({ request, ask: setRequest, done: () => setRequest(null) }),
    [request],
  );
  return <SeekContext.Provider value={value}>{children}</SeekContext.Provider>;
}

/** Asks the viewer of an entry to show a frame, paused, once it is open and ready. */
export function useAskSeek(): Seeking["ask"] {
  return useContext(SeekContext)?.ask ?? NOTHING;
}

const NOTHING = () => undefined;

/**
 * Takes the frame asked for this entry: when the session is ready it jumps there and pauses, and a window that follows
 * the presenter's clock or may not change its view drops the request instead.
 */
export function useSeekRequest(
  session: PlaybackSession,
  status: PlaybackView["status"],
  entry: string,
) {
  const seeking = useContext(SeekContext);
  const canChange = useCan("view");
  const following = useFollowing();
  // The pane the bookmark opened or brought forward is the active one; another showing the same entry leaves it be.
  const front = useContext(PaneActiveContext);
  const asked = seeking?.request?.entry === entry ? seeking.request : null;
  const done = seeking?.done;
  useEffect(() => {
    if (!asked || !done || status !== "ready" || !front) return;
    done();
    if (!canChange || following) return;
    const last = session.getSnapshot().frameCount - 1;
    // Pausing first: pausing a playing clock returns it to the frame on screen, which would undo the seek.
    session.pause();
    session.seek(Math.min(asked.frame, last));
  }, [asked, done, status, session, canChange, following, front]);
}

/**
 * The command that bookmarks the frame on screen of the entry `entry` of `item`, then shows the Bookmarks view while
 * the viewer stays in front. Does nothing before the viewer is ready.
 */
export function useAddBookmark(session: PlaybackSession, item: string, entry: string) {
  const command = useCommand();
  const setView = useSetView();
  return useCallback(() => {
    const view = session.getSnapshot();
    if (view.status !== "ready") return;
    const frame = view.shown?.sequenceId ?? view.playhead;
    const time = view.shown?.timestampUs ?? session.frameTime(frame) ?? 0;
    command({
      type: "content",
      command: {
        type: "bookmark",
        command: { type: "add", item, entry, frame, timeUs: Math.max(0, Math.round(time)) },
      },
    }).then(
      () => setView((shown) => ({ ...shown, sidebar: { view: "bookmarks", open: true } })),
      // A refused bookmark leaves the sidebar as it was, which says so.
      () => undefined,
    );
  }, [session, item, entry, command, setView]);
}

const SEEN_EPOCH = "ax-devil.sidebarEpoch";

/** The last workspace epoch this window chose a sidebar view for; a window that has not seen one starts at 0. */
function seenEpoch(): number {
  try {
    return Number(window.sessionStorage.getItem(SEEN_EPOCH)) || 0;
  } catch {
    return 0;
  }
}

/**
 * Chooses the sidebar view when a workspace replaces the one this window last saw: Workspace after a recovery,
 * else Bookmarks when it has bookmarks. The workspace the app opens at launch counts as a replacement; reloading the
 * page does not, so it keeps the view the user chose.
 */
export function useSidebarOnReplace() {
  const workspace = useDoc()?.workspace;
  const [, setSidebar] = useView("sidebar");
  const epoch = workspace?.epoch;
  const recovered = workspace?.recovered ?? false;
  const marked = (workspace?.bookmarks.length ?? 0) > 0;
  useEffect(() => {
    if (epoch === undefined || epoch === seenEpoch()) return;
    try {
      window.sessionStorage.setItem(SEEN_EPOCH, String(epoch));
    } catch {
      // Without storage a reload shows the view again, which is harmless.
    }
    if (recovered) setSidebar({ view: "workspace", open: true });
    else if (marked) setSidebar({ view: "bookmarks", open: true });
  }, [epoch, recovered, marked, setSidebar]);
}
