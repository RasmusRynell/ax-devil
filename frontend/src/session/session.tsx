import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import type { Scope } from "../commands";
import { credential } from "../access";
import type {
  Access,
  CursorOptions,
  CursorPoint,
  DocWire,
  SeatInfo,
  SeatMode,
  SessionCommand,
  ContentRef,
  Overlays,
  View,
} from "../generated/api";
import { Banner, EndedScreen } from "./Notice";
import { useNoticeSink } from "../resource";
import { useSettings } from "../settings";
import type { SessionState } from "./sessionState";
import { SessionStore } from "./store";
import { canDo } from "./views";

const StoreContext = createContext<SessionStore | null>(null);

function useStore(): SessionStore {
  const store = useContext(StoreContext);
  if (!store) throw new Error("the session hooks need a SessionProvider");
  return store;
}

/** A part of the session's state; the component renders again only when that part changes. */
function useSelect<T>(select: (state: SessionState) => T): T {
  const store = useStore();
  return useSyncExternalStore(store.subscribe, () => select(store.getSnapshot()));
}

/**
 * The window's own Session, connected for as long as it is mounted. A browser that opened a join link has nothing
 * but that session: it waits for it, and says when it is over. Tests pass their own store.
 */
export function SessionProvider({
  store: given,
  children,
}: {
  store?: SessionStore;
  children: ReactNode;
}) {
  const [store] = useState(() => given ?? new SessionStore());
  const viewer = useMemo(() => credential()?.kind === "join", []);
  const noticed = useNoticeSink();
  useEffect(() => {
    store.onChanged(noticed);
    return () => store.onChanged(null);
  }, [store, noticed]);
  useEffect(() => {
    store.start();
    return () => store.stop();
  }, [store]);
  const connection = useSyncExternalStore(store.subscribe, () => store.getSnapshot().connection);
  const ended = useSyncExternalStore(store.subscribe, () => store.getSnapshot().ended);
  const seated = useSyncExternalStore(store.subscribe, () => store.getSnapshot().seat !== null);
  const ownerAway = useSyncExternalStore(store.subscribe, () => {
    const owners = store.getSnapshot().roster.filter((seat) => seat.role === "owner");
    return owners.length > 0 && owners.every((seat) => !seat.present);
  });
  if (viewer && ended) return <EndedScreen why={ended} />;
  return (
    <StoreContext.Provider value={store}>
      {/* The notice takes a strip of its own at the bottom, so the window shrinks above it instead of being covered. */}
      <div className="flex h-screen flex-col">
        {seated || ended ? (
          children
        ) : (
          <main aria-busy="true" className="flex min-h-0 flex-1 items-center justify-center" />
        )}
        <Banner
          connection={connection}
          ownerAway={ownerAway}
          ended={ended}
          onDismiss={() => store.dismiss()}
        />
      </div>
    </StoreContext.Provider>
  );
}

/** What the Doc holds, as this seat may see it; `null` until the first one has arrived. */
export function useDoc(): DocWire | null {
  return useSelect((state) => state.doc);
}

/** Changes several parts of what the window shows in one step; see `useView`. */
export function useSetView(): (change: (shown: View) => View) => void {
  const store = useStore();
  return useCallback((change) => store.setView(change), [store]);
}

/** The way to read what the window shows right now, for a command that must decide before it asks the backend. */
export function useReadView(): () => View {
  const store = useStore();
  return useCallback(() => store.readView(), [store]);
}

/**
 * One part of what the window shows (`sidebar`, `active`, `viewers` or `layout`) and the way to change it. A change shows at
 * once and is settled by the backend later; a change the backend refuses stops showing.
 */
export function useView<K extends keyof View>(
  key: K,
): readonly [View[K], (next: View[K] | ((current: View[K]) => View[K])) => void] {
  const setView = useSetView();
  const value = useSelect((state) => state.view[key]);
  const set = useCallback(
    (next: View[K] | ((current: View[K]) => View[K])) =>
      setView((shown) => {
        const current = shown[key];
        const changed = typeof next === "function" ? next(current) : next;
        return changed === current ? shown : { ...shown, [key]: changed };
      }),
    [setView, key],
  );
  return [value, set];
}

/**
 * The overlays the window draws: those of the view it shows (the Presenter View for an Owner seat and a following
 * Guest, the seat's own for an exploring one), and the settings that start a Session while it has no view yet.
 */
export function useOverlays(): Overlays {
  const shown = useSelect((state) => (state.seat ? state.view.overlays : null));
  const { values } = useSettings();
  const objectDetails = values["overlays.object_details"] === true;
  const laneNames = values["overlays.lane_names"] === true;
  return useMemo(() => shown ?? { objectDetails, laneNames }, [shown, objectDetails, laneNames]);
}

/** How many viewers the window shows. */
export function usePanes(): number {
  return useSelect((state) => Object.keys(state.view.viewers).length);
}

/** Whether the window shows the presenter's view and nothing of its own. */
export function useFollowing(): boolean {
  return useSelect((state) => state.following);
}

/** Whether the seat is the one that goes with the Doc shown: a new Doc arrives before its seat. */
export function useSettled(): boolean {
  return useSelect((state) => state.settled);
}

/** Whether this seat may change things of this scope; the backend decides again when it is asked. */
export function useCan(scope: Scope): boolean {
  return useSelect((state) => canDo(state.seat?.can ?? null, scope));
}

/** Which scopes of commands work for this window; the others are shown disabled. */
export function useAllowed(): Record<Scope, boolean> {
  const host = useCan("host");
  const content = useCan("content");
  const view = useCan("view");
  return useMemo(() => ({ host, content, view, app: true }), [host, content, view]);
}

/** Asks the backend for a change. The promise rejects with `CommandRefused` when the backend refuses it. */
export function useCommand(): (command: SessionCommand) => Promise<void> {
  const store = useStore();
  return useCallback((command) => store.command(command), [store]);
}

/** Names the workspace as it is typed (`edit`, sent at a limited rate) and sends the last text at the end (`done`). */
export function useRenameWorkspace(): { edit: (name: string) => void; done: () => void } {
  const store = useStore();
  return useMemo(
    () => ({ edit: (name) => store.renameWorkspace(name), done: () => store.flushName() }),
    [store],
  );
}

/**
 * The way to get a ticket for a stream, for the playback socket's first message. A ticket works once, so every
 * connection asks again. `key` must be the same object between renders for as long as it names the same stream.
 * The function changes when the backend seats the window again, which ends the streams opened before: whoever opened
 * one starts over, and the backend decides again whether the window may.
 */
export function useStream(key: ContentRef): () => Promise<string> {
  const store = useStore();
  const seatings = useSelect((state) => state.seatings);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `seatings` only gives the function a new identity.
  return useCallback(() => store.requestTicket(key), [store, key, seatings]);
}

/** Whether this window is an Owner seat, which drives the Presenter View with every other Owner seat. */
export function useOwnerSeat(): boolean {
  return useSelect((state) => state.seat?.access === "owner");
}

/** Whether the window takes the Presenter View for a viewer now; see `SessionStore.takesPresenter`. */
export function useTakesPresenter(): (
  viewer: number,
  byOther: boolean,
  part?: "clock" | "viewport",
) => boolean {
  const store = useStore();
  return useCallback(
    (viewer, byOther, part) => store.takesPresenter(viewer, byOther, part),
    [store],
  );
}

/**
 * Which change of the Presenter View is the latest and whether another seat made it: the revision, and whether its
 * author is not this window's seat. The server stamps the author, so a window never has to guess by comparing values.
 */
export function usePresenterChange(): { rev: number; byOther: boolean } {
  const rev = useSelect((state) => state.seat?.presenterRev ?? -1);
  const byOther = useSelect((state) => state.seat?.presenterBy !== state.you);
  return { rev, byOther };
}

/** The backend's clock, for following a presenter: `nowUs` is undefined until it has answered. */
export function useServerClock(): { synced: boolean; nowUs: () => number | undefined } {
  const store = useStore();
  const synced = useSelect((state) => state.synced);
  return useMemo(() => ({ synced, nowUs: () => store.serverNowUs() }), [store, synced]);
}

/** What the controls around the session need: where the connection is, what this seat is, and the ways out. */
export interface SessionApi {
  /** `none` while the Session is not shared, `owner` while it is, `guest` for a seat that joined by link. */
  state: "none" | "owner" | "guest";
  /** Whether the window is an Owner seat in a Session it did not start. */
  attached: boolean;
  /** Where the connection is. */
  connection: SessionState["connection"];
  /** Why the last session ended for this window, until it is dismissed. */
  ended: SessionState["ended"];
  /** The Session this window is in, its join link when it is shared and this backend can be reached, and why not. */
  session: { id: string; you: number; link: string | null; linkProblem: string | null } | null;
  /** What Guests may do while sharing is on. */
  sharing: Access | null;
  /** Whether the window follows the presenter or explores. */
  mode: SeatMode | null;
  cursorOptions: CursorOptions;
  /** Leaves this window's Session to attach to another of the server's, with the Sessions grant. */
  attach: (session: string) => Promise<void>;
  /** Leaves this window's Session for the one a join link opens. */
  joinLink: (link: string) => void;
  /** The way out of a Session this window did not start, to a Session of its own. */
  leave: () => void;
  /** Forgets the notice about how the last session ended. */
  dismiss: () => void;
  /** Exploring Guest: goes back to the presenter's view. */
  follow: () => Promise<void>;
  setCursorOptions: (options: CursorOptions) => void;
  /** Tells the others where this cursor is, or `null` when it left the window. */
  moveCursor: (at: CursorPoint | null) => void;
}

/** The window's session and the actions on it. */
export function useSession(): SessionApi {
  const store = useStore();
  const access = useSelect((state) => state.seat?.access ?? null);
  const sharing = useSelect((state) => state.seat?.sharing?.access ?? null);
  const link = useSelect((state) => state.seat?.sharing?.link ?? null);
  const linkProblem = useSelect((state) => state.seat?.sharing?.linkProblem ?? null);
  const mode = useSelect((state) => state.seat?.mode ?? null);
  const id = useSelect((state) => state.session);
  const you = useSelect((state) => state.you);
  const attached = useSelect((state) => state.attached && state.seat?.access === "owner");
  const connection = useSelect((state) => state.connection);
  const ended = useSelect((state) => state.ended);
  const cursorOptions = useSelect((state) => state.cursorOptions);
  return useMemo(
    () => ({
      state: access === "owner" ? (sharing ? "owner" : "none") : access ? "guest" : "none",
      attached,
      connection,
      ended,
      session: id !== null && you !== null ? { id, you, link, linkProblem } : null,
      sharing,
      mode,
      cursorOptions,
      attach: (session) => store.attach(session),
      joinLink: (text) => store.joinLink(text),
      leave: () => store.leave(),
      dismiss: () => store.dismiss(),
      follow: () => store.follow(),
      setCursorOptions: (options) => store.setCursorOptions(options),
      moveCursor: (at) => store.moveCursor(at),
    }),
    [
      store,
      access,
      attached,
      sharing,
      link,
      linkProblem,
      mode,
      id,
      you,
      connection,
      ended,
      cursorOptions,
    ],
  );
}

/** Everyone in the Session, and which of them is this window. */
export function useParticipants(): { you: number | null; participants: readonly SeatInfo[] } {
  const you = useSelect((state) => state.you);
  const participants = useSelect((state) => state.roster);
  return { you, participants };
}

/** The other participants' cursors by participant id, replaced on every move. */
export function useCursors(): ReadonlyMap<number, CursorPoint> {
  const { cursors } = useStore();
  return useSyncExternalStore(cursors.subscribe, cursors.snapshot);
}
