import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { credential } from "../access";
import type {
  CursorOptions,
  CursorPoint,
  SeatInfo,
  ShareClientMessage,
  ShareServerMessage,
  ShareSnapshot,
  ViewState,
} from "../generated/api";
import { connect, type Connection, type Ending, type Entry, type Status } from "./connection";
import { Banner, EndedScreen } from "./Notice";

/** The tab's seat (its session and resume token), so a reload within the grace period takes it back. Another tab has its own. */
const STORED_SEAT = "ax-devil.seat";

export interface SessionApi {
  /** `none` until a session's first snapshot has arrived: private is the default and needs no connection. */
  state: "none" | "owner" | "watcher";
  /** Where the connection is; `idle` while there is none. */
  connection: "idle" | Status["state"];
  /** Why the last session ended for this tab, until it is dismissed. */
  ended: Ending | null;
  /** The session this tab is in, and its join link when the backend can be reached by other machines. */
  session: { id: string; you: number; link: string | null } | null;
  /** What this tab last heard of its session; frozen while the connection is down. */
  snapshot: ShareSnapshot | null;
  cursorOptions: CursorOptions;
  /** Starts a session and owns it. */
  create: () => void;
  /** Joins the session a join link opens: its `<id>.<secret>`, or the whole link. */
  joinLink: (link: string) => void;
  /** Watches the session `id`, with the Sessions grant. */
  watch: (id: string) => void;
  /** The owner's way out, which ends the session for everyone. */
  stop: () => void;
  /** A watcher's way out. */
  leave: () => void;
  /** Forgets the notice about how the last session ended. */
  dismiss: () => void;
  /** Owner: whether watchers may explore on their own. */
  setExplore: (explore: boolean) => void;
  /** Owner: invalidates the join link and gets a new one. */
  regenerateLink: () => void;
  /** Watcher: shows its own view of what the owner has open. */
  explore: () => void;
  /** Exploring watcher: goes back to the presenter's view. */
  follow: () => void;
  setCursorOptions: (options: CursorOptions) => void;
  /** Tells the others where this cursor is, or `null` when it left the window. */
  moveCursor: (at: CursorPoint | null) => void;
  /** Owner: shows `state` to everyone following. */
  publish: (state: ViewState) => void;
  /** Exploring watcher: shows `state` in this seat's own view; `seq` counts these edits. */
  setView: (state: ViewState, seq: number) => void;
}

/** The other participants' cursors. Held outside React state so a moving cursor never re-renders the shell. */
class Cursors {
  private current: ReadonlyMap<number, CursorPoint> = new Map();
  private listeners = new Set<() => void>();

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => void this.listeners.delete(listener);
  };
  snapshot = () => this.current;

  /** Moves a participant's cursor, or removes it. */
  set(participant: number, at: CursorPoint | null) {
    const next = new Map(this.current);
    if (at) next.set(participant, at);
    else next.delete(participant);
    this.replace(next);
  }
  /** Drops the cursors of everyone not in `participants`. */
  keepOnly(participants: readonly number[]) {
    const kept = [...this.current].filter(([id]) => participants.includes(id));
    if (kept.length !== this.current.size) this.replace(new Map(kept));
  }
  private replace(next: ReadonlyMap<number, CursorPoint>) {
    this.current = next;
    this.listeners.forEach((listener) => listener());
  }
}

interface Context extends SessionApi {
  cursors: Cursors;
}

interface Seat {
  id: string;
  you: number;
  link: string | null;
  role: "owner" | "watcher";
}

const SessionContext = createContext<Context | null>(null);

/** The id and secret of a join link pasted whole or as `<id>.<secret>`. */
const linkOf = (text: string) => decodeURIComponent(text.trim().replace(/^.*#join=/, ""));

interface StoredSeat {
  session: string;
  token: string;
}

function storedSeat(): StoredSeat | null {
  const stored = window.sessionStorage.getItem(STORED_SEAT);
  return stored ? (JSON.parse(stored) as StoredSeat) : null;
}

/**
 * The tab's session. Nothing is connected until the user creates or joins one (or opens a join link, or reloads a
 * tab that was in one); then one WebSocket carries it, keeps it through drops and reports why it ended. The state
 * is `none | owner | watcher`; what each of them may change is decided in `view.ts`.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentity] = useState<Seat | null>(null);
  const [snapshot, setSnapshot] = useState<ShareSnapshot | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [cursorOptions, setOptions] = useState<CursorOptions>({ show: true, share: true });
  const [cursors] = useState(() => new Cursors());
  const connection = useRef<Connection | null>(null);
  const options = useRef(cursorOptions);
  const viewer = useMemo(() => credential()?.kind === "join", []);

  const api = useMemo(() => {
    const clear = () => {
      window.sessionStorage.removeItem(STORED_SEAT);
      setIdentity(null);
      setSnapshot(null);
      cursors.keepOnly([]);
    };
    const onMessage = (message: ShareServerMessage) => {
      if (message.type === "welcome") {
        const seat: StoredSeat = { session: message.session, token: message.resume };
        window.sessionStorage.setItem(STORED_SEAT, JSON.stringify(seat));
        setIdentity({
          id: message.session,
          you: message.you,
          link: message.link,
          role: message.role,
        });
      } else if (message.type === "snapshot") {
        cursors.keepOnly(message.participants.map((participant) => participant.id));
        setSnapshot(message);
      } else if (message.type === "cursor") cursors.set(message.participant, message.at);
      else if (message.type === "link") setIdentity((now) => now && { ...now, link: message.url });
    };
    const begin = (entry: Entry | null, resume?: string) => {
      connection.current?.close();
      clear();
      setStatus({ state: "connecting" });
      const held = credential();
      connection.current = connect({
        entry,
        resume,
        password: held?.kind === "password" ? held.password : undefined,
        cursors: () => options.current,
        onMessage,
        onStatus: (next) => {
          setStatus(next);
          if (next.state === "ended") {
            connection.current = null;
            clear();
          }
        },
      });
    };
    const send = (message: ShareClientMessage) => connection.current?.send(message);
    const leave = () => {
      send({ type: "leave" });
      connection.current?.close();
      connection.current = null;
      clear();
      setStatus(null);
    };
    return {
      cursors,
      begin,
      create: () => begin({ kind: "create" }),
      joinLink: (link: string) => begin({ kind: "join", link: linkOf(link) }),
      watch: (session: string) => begin({ kind: "watch", session }),
      stop: leave,
      leave,
      dismiss: () => setStatus((now) => (now?.state === "ended" ? null : now)),
      setExplore: (explore: boolean) => send({ type: "setOptions", explore }),
      regenerateLink: () => send({ type: "regenerateLink" }),
      explore: () => send({ type: "explore" }),
      follow: () => send({ type: "follow" }),
      setCursorOptions: (next: CursorOptions) => {
        options.current = next;
        setOptions(next);
        send({ type: "cursorOptions", ...next });
      },
      moveCursor: (at: CursorPoint | null) => send({ type: "cursor", at }),
      publish: (state: ViewState) => send({ type: "publish", state }),
      setView: (state: ViewState, seq: number) => send({ type: "setView", state, seq }),
    };
  }, [cursors]);

  // A join link in the address, or a seat a reload left behind, connects without being asked. The seat is taken
  // back only when it is in the session the link names; a link to another session is joined as a new seat.
  useEffect(() => {
    const held = credential();
    const seat = storedSeat();
    const entry: Entry | null =
      held?.kind === "join" ? { kind: "join", link: `${held.session}.${held.secret}` } : null;
    const resume = seat && (!held || held.kind !== "join" || held.session === seat.session);
    if (entry || resume) api.begin(entry, resume ? seat.token : undefined);
    return () => {
      connection.current?.close();
      connection.current = null;
    };
  }, [api]);

  const live = identity && snapshot;
  const { begin: _begin, ...actions } = api;
  const value: Context = {
    ...actions,
    state: live ? identity.role : "none",
    connection: status?.state ?? "idle",
    ended: status?.state === "ended" ? status.why : null,
    session: identity,
    snapshot,
    cursorOptions,
  };
  // A browser that opened a join link has nothing but that session: wait for it, and say when it is over. The first
  // render is still `idle`, before the connection has begun.
  if (viewer && !live && (value.connection === "idle" || value.connection === "connecting"))
    return <main aria-busy="true" className="flex h-screen items-center justify-center" />;
  if (viewer && value.ended) return <EndedScreen why={value.ended} />;
  return (
    <SessionContext.Provider value={value}>
      {children}
      <Banner
        connection={value.connection}
        ownerAway={snapshot?.ownerPresent === false}
        ended={value.ended}
        onDismiss={api.dismiss}
      />
    </SessionContext.Provider>
  );
}

/** The tab's session and the actions on it; only inside a `SessionProvider`. */
export function useSession(): SessionApi {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession needs a SessionProvider");
  return context;
}

/** Everyone in the session, and which of them is this tab; empty outside a session. */
export function useParticipants(): { you: number | null; participants: readonly SeatInfo[] } {
  const { session, snapshot } = useSession();
  return { you: session?.you ?? null, participants: snapshot?.participants ?? [] };
}

/** The other participants' cursors by participant id, replaced on every move. */
export function useCursors(): ReadonlyMap<number, CursorPoint> {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useCursors needs a SessionProvider");
  return useSyncExternalStore(context.cursors.subscribe, context.cursors.snapshot);
}
