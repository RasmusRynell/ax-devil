import { credential } from "../client/access";
import { CommandFailed, CommandRefused } from "../client/errors";
import type {
  CursorOptions,
  CursorPoint,
  DocWire,
  SeatInfo,
  SessionCommand,
  SessionServerMessage,
  ContentRef,
  Topic,
  View,
} from "../generated/api";
import { OffsetEstimator } from "./timeSync";
import { connect, type Connection, type Ending, type Entry, type Status } from "./connection";
import { Cursors } from "./cursors";
import { STORED_SEAT, readStoredSeat } from "./storedSeat";
import type { SeatPart, SessionState } from "./sessionState";
import { EMPTY_VIEW, canDo, shareView, shownView, type Pending } from "./views";

/** Round trips at each connect, then one per period. */
const SYNC_BURST = 5;
const SYNC_EVERY_MS = 10_000;
/** The least time between two renames sent: the session takes ten commands a second and closes the socket above it. */
const RENAME_EVERY_MS = 200;

export interface SessionDeps {
  connect: typeof connect;
  /** Monotonic milliseconds. */
  now: () => number;
  /** Calls back once at the next display refresh; returns the way to cancel. */
  frame: (callback: () => void) => () => void;
}

const browser: SessionDeps = {
  connect,
  now: () => performance.now(),
  frame: (callback) => {
    const handle = requestAnimationFrame(callback);
    return () => cancelAnimationFrame(handle);
  },
};

interface Asked {
  resolve: (ticket?: string) => void;
  reject: (reason: Error) => void;
}

/**
 * The window's own Session on the backend: one socket, always connected, projected for the components. The backend
 * owns the Doc and every seat's standing; this keeps what arrived, the changes of the window's view it has not seen
 * settled, and the commands waiting for their answer.
 */
export class SessionStore {
  readonly cursors = new Cursors();
  readonly #deps: SessionDeps;
  readonly #listeners = new Set<() => void>();
  readonly #asked = new Map<number, Asked>();
  #state: SessionState = {
    connection: "connecting",
    ended: null,
    session: null,
    you: null,
    attached: false,
    doc: null,
    seat: null,
    roster: [],
    cursorOptions: { show: true, share: true },
    view: EMPTY_VIEW,
    following: false,
    settled: false,
    synced: false,
    seatings: 0,
  };
  #connection: Connection | null = null;
  /** The connection of an `attach` that is not admitted yet; the window stays in its seat until it is. */
  #candidate: Connection | null = null;
  #doc: { rev: number; wire: DocWire } | null = null;
  /** A seat part whose Doc has not arrived; the backend sends the Doc first, so this only guards the order. */
  #waiting: SeatPart | null = null;
  #pending: Pending | null = null;
  /** The highest view number this window has used, and the highest it has sent. */
  #seq = 0;
  #sent = 0;
  #commandSeq = 0;
  /** The parts of the Presenter View (`clock`, `viewport`) the window has looked at, by viewer, in this Session. */
  #seen = new Set<string>();
  #cancelFrame: (() => void) | null = null;
  #own = true;
  #estimator = new OffsetEstimator();
  #syncId = 0;
  #syncLeft = 0;
  #syncTimer: ReturnType<typeof setTimeout> | undefined;
  #waiters: Asked[] = [];
  /** The newest name typed and not sent, the timer that sends it, and when a name was last sent. */
  #name: string | null = null;
  #nameTimer: ReturnType<typeof setTimeout> | undefined;
  #nameSentAt = -RENAME_EVERY_MS;
  #onChanged: ((topic: Topic, revision: number, fresh: boolean) => void) | null = null;
  /** Whether no notice has come since the last welcome: the first of a connection starts a new revision order. */
  #freshNotice = false;

  constructor(deps: Partial<SessionDeps> = {}) {
    this.#deps = { ...browser, ...deps };
  }

  /** Runs *listener* with each `Changed` notice the backend sends an Owner's connection; null stops it. */
  onChanged(listener: ((topic: Topic, revision: number, fresh: boolean) => void) | null): void {
    this.#onChanged = listener;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  getSnapshot = (): SessionState => this.#state;

  /**
   * Connects the way this tab is entered: by the join link it holds, else as its own Session, taking back the seat a
   * reload left behind. The seat of a Session someone else started, once gone, is not replaced by a Session of its own.
   */
  start(): void {
    const held = credential();
    const stored = readStoredSeat();
    const link = held?.kind === "join" ? `${held.session}.${held.secret}` : null;
    const resume =
      stored && (held?.kind !== "join" || held.session === stored.session) ? stored : null;
    const entry: Entry | null = link
      ? { kind: "join", link }
      : resume && !resume.own
        ? null
        : { kind: "own" };
    this.#begin(entry, resume?.token, resume?.own ?? true);
  }

  /** Ends the connection without telling the session; the seat stays for its grace period. */
  stop(): void {
    this.#connection?.close();
    this.#connection = null;
    this.#candidate?.close();
    this.#candidate = null;
    this.#cancelFrame?.();
    this.#cancelFrame = null;
    clearTimeout(this.#syncTimer);
    this.#dropName();
    this.#refuse(new Error("The window closed."));
  }

  /** Attaches to another of the server's Sessions. The window keeps its seat until the new one is admitted; a refusal rejects. */
  attach(session: string): Promise<void> {
    this.#candidate?.close();
    return new Promise((resolve, reject) => {
      const held = credential();
      const candidate: Connection = this.#deps.connect({
        entry: { kind: "attach", session },
        password: held?.kind === "password" ? held.password : undefined,
        cursors: () => this.#state.cursorOptions,
        onMessage: (message) => {
          if (candidate !== this.#connection && message.type === "welcome") {
            this.#candidate = null;
            this.#leaveSession();
            this.#adopt(candidate);
            resolve();
          }
          if (candidate === this.#connection) this.#receive(message);
        },
        onStatus: (status) => {
          if (candidate === this.#connection) this.#onStatus(status);
          else if (status.state === "ended")
            reject(new Error("That session is no longer available."));
        },
      });
      this.#candidate = candidate;
    });
  }

  /** Leaves this window's Session for the one a join link opens: its `<id>.<secret>`, or the whole link. */
  joinLink(link: string): void {
    this.#leaveSession();
    const secret = decodeURIComponent(link.trim().replace(/^.*#join=/, ""));
    this.#begin({ kind: "join", link: secret }, undefined, false);
  }

  /** The way out of a Session this window did not start, to a new Session of its own. */
  leave(): void {
    this.#leaveSession();
    this.#begin({ kind: "own" }, undefined, true);
  }

  /** Forgets the notice about how the session ended and starts a new Session of its own. */
  dismiss(): void {
    if (this.#state.ended) this.#begin({ kind: "own" }, undefined, true);
  }

  /** What the window shows right now. */
  readView(): View {
    return this.#state.view;
  }

  /** Changes what the window shows. It shows at once; the backend settles it later or refuses it. */
  setView(change: (shown: View) => View): void {
    const shown = this.#state.view;
    const next = change(shown);
    if (next === shown || !canDo(this.#state.seat?.can ?? null, "view")) return;
    this.#seq += 1;
    this.#pending = { seq: this.#seq, view: next };
    this.#cancelFrame ??= this.#deps.frame(() => {
      this.#cancelFrame = null;
      this.#flush();
    });
    this.#publish();
  }

  /**
   * Whether the window takes `part` of the Presenter View as it is for `viewer`: on its first look in this Session, or
   * when another seat made the change; never over a change of its own the backend has not settled.
   */
  takesPresenter(viewer: number, byOther: boolean, part: "clock" | "viewport" = "clock"): boolean {
    const key = `${part}/${viewer}`;
    const first = !this.#seen.has(key);
    this.#seen.add(key);
    return !this.#pending && (first || byOther);
  }

  /** Back to the presenter's view; a change that was on its way is dropped. */
  follow(): Promise<void> {
    this.#cancelFrame?.();
    this.#cancelFrame = null;
    this.#pending = null;
    this.#publish();
    return this.command({ type: "follow" });
  }

  /** Asks the backend for a change; settles when it answers and rejects with `CommandRefused` when it refuses. */
  command(command: SessionCommand): Promise<void> {
    this.flushName();
    return this.#ask(command).then(() => undefined);
  }

  /**
   * Names the workspace as it is typed. Only the newest name waits, and names go out at most every
   * `RENAME_EVERY_MS`. A refusal or a lost connection leaves the label as the backend has it.
   */
  renameWorkspace(name: string): void {
    this.#name = name;
    if (this.#nameTimer !== undefined) return;
    const wait = this.#nameSentAt + RENAME_EVERY_MS - this.#deps.now();
    if (wait <= 0) this.flushName();
    else this.#nameTimer = setTimeout(() => this.flushName(), wait);
  }

  /** Sends the name that waits, so the next command sees the latest text. */
  flushName(): void {
    const name = this.#name;
    this.#dropName();
    const epoch = this.#doc?.wire.workspace.epoch;
    if (name === null || epoch === undefined) return;
    this.#nameSentAt = this.#deps.now();
    this.#ask({ type: "content", command: { type: "renameWorkspace", name, epoch } }).catch(
      () => {},
    );
  }

  #dropName(): void {
    clearTimeout(this.#nameTimer);
    this.#nameTimer = undefined;
    this.#name = null;
  }

  /** A ticket for one playback socket, once the session is connected; every socket needs its own. */
  async requestTicket(key: ContentRef): Promise<string> {
    await new Promise<void>((resolve, reject) => {
      if (this.#state.connection === "online" && this.#state.session) resolve();
      else this.#waiters.push({ resolve: () => resolve(), reject });
    });
    const ticket = await this.#ask({ type: "requestTicket", key });
    if (ticket === undefined) throw new Error("The backend sent no ticket.");
    return ticket;
  }

  setCursorOptions(options: CursorOptions): void {
    this.#state = { ...this.#state, cursorOptions: options };
    this.#connection?.send({ type: "cursorOptions", ...options });
    this.#notify();
  }

  /** Tells the others where this cursor is, or `null` when it left the window. */
  moveCursor(at: CursorPoint | null): void {
    this.#connection?.send({ type: "cursor", at });
  }

  /** The backend's clock now, in microseconds; undefined until it has answered a time sync. */
  serverNowUs(): number | undefined {
    return this.#estimator.serverNowUs(this.#deps.now());
  }

  #ask(command: SessionCommand): Promise<string | undefined> {
    return new Promise((resolve, reject) => {
      const seq = ++this.#commandSeq;
      this.#asked.set(seq, { resolve, reject });
      if (!this.#connection?.send({ type: "command", seq, cmd: command })) {
        this.#asked.delete(seq);
        reject(new Error("The connection is down."));
      }
    });
  }

  /** Rejects whatever waits for the backend: its answer will never come. */
  #refuse(reason: Error): void {
    const waiting = [...this.#asked.values(), ...this.#waiters];
    this.#asked.clear();
    this.#waiters = [];
    for (const { reject } of waiting) reject(reason);
  }

  #leaveSession(): void {
    this.#connection?.send({ type: "leave" });
  }

  #begin(entry: Entry | null, resume: string | undefined, own: boolean): void {
    this.#reset(own);
    const held = credential();
    this.#state = { ...this.#state, connection: "connecting", ended: null };
    this.#connection = this.#deps.connect({
      entry,
      resume,
      password: held?.kind === "password" ? held.password : undefined,
      cursors: () => this.#state.cursorOptions,
      onMessage: (message) => this.#receive(message),
      onStatus: (status) => this.#onStatus(status),
    });
    this.#publish();
  }

  /** Makes an admitted `attach` connection the window's, in place of the one it leaves. */
  #adopt(connection: Connection): void {
    this.#reset(false);
    this.#state = { ...this.#state, connection: "online", ended: null };
    this.#connection = connection;
  }

  /** Drops the window's connection and Session, for the one that replaces it. */
  #reset(own: boolean): void {
    this.#candidate?.close();
    this.#candidate = null;
    this.#connection?.close();
    clearTimeout(this.#syncTimer);
    this.#refuse(new Error("The session changed."));
    window.sessionStorage.removeItem(STORED_SEAT);
    this.#own = own;
    this.#clear();
    this.#state = { ...this.#state, attached: !own };
  }

  /** Forgets everything of the session this window was in. */
  #clear(): void {
    this.#cancelFrame?.();
    this.#cancelFrame = null;
    this.#doc = null;
    this.#waiting = null;
    this.#pending = null;
    this.#dropName();
    this.#seen.clear();
    this.#seq = 0;
    this.#sent = 0;
    this.#estimator = new OffsetEstimator();
    this.#state = {
      ...this.#state,
      session: null,
      you: null,
      doc: null,
      seat: null,
      roster: [],
      synced: false,
    };
    this.cursors.keepOnly([]);
  }

  #onStatus(status: Status): void {
    if (status.state === "ended") {
      this.#connection = null;
      clearTimeout(this.#syncTimer);
      window.sessionStorage.removeItem(STORED_SEAT);
      this.#refuse(new Error("The session is over."));
      this.#clear();
      this.#state = { ...this.#state, connection: "connecting", ended: status.why };
    } else {
      if (status.state === "reconnecting") {
        clearTimeout(this.#syncTimer);
        this.#refuse(new Error("The connection was lost."));
      }
      this.#state = { ...this.#state, connection: status.state };
    }
    this.#publish();
  }

  #receive(message: SessionServerMessage): void {
    switch (message.type) {
      case "welcome":
        return this.#welcome(message);
      case "doc":
        if (this.#doc && message.docRev < this.#doc.rev) return;
        // A name typed for the workspace that was replaced must not rename its replacement.
        if (this.#doc?.wire.workspace.epoch !== message.doc.workspace.epoch) this.#dropName();
        this.#doc = { rev: message.docRev, wire: message.doc };
        if (this.#waiting && this.#waiting.docRev <= message.docRev) this.#takeSeat(this.#waiting);
        break;
      case "seat":
        // The Doc comes first on the wire; a part that points at a newer Doc than this one waits for it.
        if (message.docRev > (this.#doc?.rev ?? -1)) this.#waiting = message;
        else this.#takeSeat(message);
        break;
      case "roster":
        this.cursors.keepOnly(message.participants.map((participant) => participant.id));
        this.#state = { ...this.#state, roster: message.participants };
        break;
      case "ack":
        return this.#answer(message.seq, message.outcome);
      case "ticket":
        return this.#answer(message.seq, { status: "ok" }, message.ticket);
      case "timeSyncReply":
        return this.#synced(message);
      case "cursor":
        return this.cursors.set(message.participant, message.at);
      case "changed": {
        const fresh = this.#freshNotice;
        this.#freshNotice = false;
        return this.#onChanged?.(message.topic, message.revision, fresh);
      }
      case "heartbeat":
      case "ended":
        return;
    }
    this.#publish();
  }

  #takeSeat(seat: SeatPart): void {
    this.#waiting = null;
    this.#state = { ...this.#state, seat };
  }

  #welcome(message: Extract<SessionServerMessage, { type: "welcome" }>): void {
    this.#freshNotice = true;
    if (message.session !== this.#state.session) {
      // Not the Session this window was in: nothing of the old one carries over.
      this.#clear();
    }
    const stored = { session: message.session, token: message.resume, own: this.#own };
    window.sessionStorage.setItem(STORED_SEAT, JSON.stringify(stored));
    // Views numbered up to `viewsDone` are settled, and the backend's count outlives a reload. The newest change
    // that is not settled goes out again under a number above it.
    this.#seq = Math.max(this.#seq, message.viewsDone);
    if (this.#pending && this.#pending.seq > message.viewsDone) {
      this.#seq += 1;
      this.#pending = { ...this.#pending, seq: this.#seq };
      this.#sent = this.#seq - 1;
      this.#cancelFrame ??= this.#deps.frame(() => {
        this.#cancelFrame = null;
        this.#flush();
      });
    } else {
      this.#pending = null;
      this.#sent = this.#seq;
    }
    this.#state = {
      ...this.#state,
      session: message.session,
      you: message.seat,
      seatings: this.#state.seatings + 1,
    };
    this.#syncLeft = SYNC_BURST;
    this.#ping();
    for (const { resolve } of this.#waiters.splice(0)) resolve();
    this.#publish();
  }

  #flush(): void {
    const pending = this.#pending;
    if (pending && pending.seq > this.#sent) {
      if (this.#connection?.send({ type: "view", seq: pending.seq, view: pending.view }))
        this.#sent = pending.seq;
    }
  }

  #answer(
    seq: number,
    outcome: Extract<SessionServerMessage, { type: "ack" }>["outcome"],
    ticket?: string,
  ): void {
    const asked = this.#asked.get(seq);
    this.#asked.delete(seq);
    if (outcome.status === "ok") asked?.resolve(ticket);
    else if (outcome.status === "failed") asked?.reject(new CommandFailed(outcome.error));
    else asked?.reject(new CommandRefused(outcome.code));
  }

  #ping(): void {
    this.#syncId += 1;
    const clientUs = Math.round(this.#deps.now() * 1000);
    this.#connection?.send({ type: "timeSync", id: this.#syncId, clientUs });
  }

  #synced(reply: Extract<SessionServerMessage, { type: "timeSyncReply" }>): void {
    if (reply.id !== this.#syncId) return;
    const receivedUs = this.#deps.now() * 1000;
    this.#estimator.add({ sentUs: reply.clientUs, receivedUs, serverUs: reply.serverUs });
    if (this.#syncLeft > 1) {
      this.#syncLeft -= 1;
      this.#ping();
    } else {
      this.#syncLeft = 1;
      this.#syncTimer = setTimeout(() => this.#ping(), SYNC_EVERY_MS);
    }
    this.#publish();
  }

  /** Brings everything derived from what arrived up to date and tells the readers. */
  #publish(): void {
    const { seat } = this.#state;
    // The backend settles a change by raising `viewsDone` past its number, whether it applied or refused it.
    if (seat && this.#pending && this.#pending.seq <= seat.viewsDone) this.#pending = null;
    const view = shareView(this.#state.view, shownView(seat?.view, this.#pending));
    this.#state = {
      ...this.#state,
      doc: this.#doc?.wire ?? null,
      view,
      following: !!seat && seat.access !== "owner" && seat.mode === "following" && !this.#pending,
      settled: !!seat && seat.docRev === this.#doc?.rev,
      synced: this.serverNowUs() !== undefined,
    };
    this.#notify();
  }

  #notify(): void {
    this.#listeners.forEach((listener) => listener());
  }
}
