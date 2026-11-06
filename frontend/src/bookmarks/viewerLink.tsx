import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import type { PlaybackSession } from "../playback/session";
import type { PlaybackView } from "../playback/view";
import { useCan, useCommand, useDoc, useFollowing, useSetView, useView } from "../session/session";

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
  const asked = seeking?.request?.entry === entry ? seeking.request : null;
  const done = seeking?.done;
  useEffect(() => {
    if (!asked || !done || status !== "ready") return;
    done();
    if (!canChange || following) return;
    const last = session.getSnapshot().frameCount - 1;
    session.seek(Math.min(asked.frame, last));
    session.pause();
  }, [asked, done, status, session, canChange, following]);
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

/**
 * Shows the Bookmarks view when another workspace replaces this one and has bookmarks. The workspace the window
 * starts with changes nothing, so reloading the page keeps the view the user chose.
 */
export function useBookmarksOnReplace() {
  const workspace = useDoc()?.workspace;
  const [, setSidebar] = useView("sidebar");
  const seen = useRef<number | undefined>(undefined);
  const epoch = workspace?.epoch;
  const marked = (workspace?.bookmarks.length ?? 0) > 0;
  useEffect(() => {
    const before = seen.current;
    seen.current = epoch;
    if (before !== undefined && epoch !== before && marked)
      setSidebar({ view: "bookmarks", open: true });
  }, [epoch, marked, setSidebar]);
}
