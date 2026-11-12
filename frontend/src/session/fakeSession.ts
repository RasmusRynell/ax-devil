import type {
  Access,
  CursorPoint,
  ErrorWire,
  RefusalCode,
  SeatAccess,
  SessionClientMessage,
  SessionCommand,
  SessionServerMessage,
  View,
} from "../generated/api";
import { type Scenario, Showing, scenario, seatAccess } from "./fakeScenario";
import { EMPTY_VIEW } from "./views";

interface Seat {
  id: number;
  owner: boolean;
  /** The seat's own view while it explores; null while it follows. */
  view: View | null;
  viewsDone: number;
  resume: string;
  socket: FakeSocket | null;
}

/**
 * The commands that change the Doc but have no recorded step, each for one reason. Random ids: `addItems`,
 * `addResolved`, `editResolved` (a staged playlist), `bookmark.add`. Files read or written: `editItem.video`, `open`,
 * `save`, `exportBookmarks`. A test sees their ack and no new Doc.
 */
const UNRECORDED = new Set([
  "addItems",
  "addResolved",
  "editItem.video",
  "editResolved",
  "bookmark.add",
  "open",
  "save",
  "exportBookmarks",
]);

/** The name `UNRECORDED` knows a content or workspace command by. */
const nameOf = (cmd: Extract<SessionCommand, { type: "content" | "workspace" }>): string => {
  if (cmd.type !== "content") return cmd.command.type;
  if (cmd.command.type === "bookmark") return `bookmark.${cmd.command.command.type}`;
  if (cmd.command.type === "editItem") return `editItem.${cmd.command.new.type.toLowerCase()}`;
  return cmd.command.type;
};

/** The workspace a session starts with unless a test says otherwise: an empty one. */
const launched = () => ({ scenario: scenario("empty"), state: "initial" });

/**
 * One Session of the fake backend, as the real one keeps it: the Doc, the presenter's view, sharing, and a seat per
 * participant with its own view while it explores. Tests drive the owner through it when the client under test is a
 * Guest.
 */
export class Room {
  /** The scenario this room shows, and the state it is in. */
  shown = new Showing(FakeSocket.launch.scenario, FakeSocket.launch.state);
  docRev = 0;
  presenter: View = EMPTY_VIEW;
  /** Counts changes to the presenter's view, and the seat that made the last one (none: the backend). */
  presenterRev = 0;
  presenterBy: number | undefined;
  /** What Guests may do while sharing is on; null while it is off. */
  access: Access | null = null;
  secret = "s3cret";
  readonly seats: Seat[] = [];
  constructor(readonly id: string) {}

  /** What a join link for this session holds. */
  get link() {
    return `${this.id}.${this.secret}`;
  }

  /** Adds a participant: an Owner seat, or a Guest. */
  seat(socket: FakeSocket | null, owner: boolean): Seat {
    const id = this.seats.length + 1;
    const seat: Seat = { id, owner, view: null, viewsDone: 0, resume: "", socket };
    this.seats.push(seat);
    return seat;
  }

  /** Shows a backend-written scenario in one of its states. */
  show(s: Scenario, state = "initial") {
    this.shown = new Showing(s, state);
    const view = s.view(state);
    if (view) {
      this.presenter = view;
      this.presenterRev += 1;
      this.presenterBy = undefined;
    }
    this.docRev += 1;
    this.share();
  }

  /** The Owner seat `by` shows another view. */
  publish(view: View, by?: number) {
    this.presenter = view;
    this.presenterRev += 1;
    this.presenterBy = by;
    this.share();
  }

  /** An Owner seat changes what Guests may do; a Guest that may not explore goes back to following. */
  allow(access: Access | null) {
    this.access = access;
    if (access === "watch") this.seats.forEach((seat) => (seat.view = null));
    this.share();
  }

  /** Sends every connected seat its own part. */
  share() {
    this.seats.forEach((seat) => seat.socket?.push());
  }
}

/**
 * Stands in for `WebSocket` and the backend behind it: seats a `Hello`, answers with `Welcome`, the Doc, the seat's
 * part and the roster, applies what each seat may send, and closes with the codes the real socket uses. Install it
 * with `vi.stubGlobal("WebSocket", FakeSocket)` and call `FakeSocket.reset()` between tests.
 */
export class FakeSocket {
  static readonly OPEN = 1;
  static sockets: FakeSocket[] = [];
  static rooms = new Map<string, Room>();
  static tokens = 0;
  /** The scenario state a new session starts in, as when the app opens a file at launch. */
  static launch = launched();
  /** The backend's clock minus the client's, in microseconds. */
  static clockOffsetUs = 5_000_000;
  /** While set, the backend refuses every change to the workspace with this code. */
  static refuseContent: RefusalCode | null = null;
  /** While set, the backend accepts a change to the workspace but fails it with this error. */
  static failContent: ErrorWire | null = null;
  /** The workspace files that exist on the fake host; a Save onto one of them fails unless it replaces. */
  static existingFiles = new Set<string>();
  /** While true, the backend refuses every stream ticket, as it does for content the Doc no longer lets the seat play. */
  static refuseTickets = false;
  /** The server password the fake backend asks of a caller who is not joining by a link; null for a server without one. */
  static serverPassword: string | null = null;
  /** The commands that changed the Doc in the backend, or would not be accepted, though no recorded step says so. */
  static unrecorded: string[] = [];

  /** Clears the fake between tests; fails the test that sent a command with no recorded step and no exemption. */
  static reset() {
    const missed = FakeSocket.unrecorded.splice(0);
    FakeSocket.refuseTickets = false;
    FakeSocket.existingFiles.clear();
    FakeSocket.refuseContent = null;
    FakeSocket.failContent = null;
    FakeSocket.serverPassword = null;
    FakeSocket.sockets = [];
    FakeSocket.rooms.clear();
    FakeSocket.launch = launched();
    FakeSocket.clockOffsetUs = 5_000_000;
    if (missed.length)
      throw new Error(
        `no recorded step for ${missed.join("; ")}: record it in the Rust scenario, or use another state`,
      );
  }

  /** A session someone else owns and shares, for the client under test to join or attach to. */
  static host(access: Access = "watch"): Room {
    const room = new Room(`room-${FakeSocket.rooms.size + 1}`);
    room.access = access;
    room.seat(null, true);
    FakeSocket.rooms.set(room.id, room);
    return room;
  }

  /** The latest socket, the one the client is using now. */
  static get latest(): FakeSocket {
    return FakeSocket.sockets[FakeSocket.sockets.length - 1]!;
  }

  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  /** Every message the client sent, as the backend received it. */
  readonly received: SessionClientMessage[] = [];
  /** While true, what the client sends waits for `flush()`: the backend has not got to it yet. */
  defer = false;
  private queue: SessionClientMessage[] = [];
  private room: Room | null = null;
  private seat: Seat | null = null;
  private sentDoc = -1;

  constructor(readonly url: string) {
    FakeSocket.sockets.push(this);
    queueMicrotask(() => {
      this.readyState = FakeSocket.OPEN;
      this.onopen?.();
    });
  }

  /** What the client sends. */
  send(text: string) {
    const message = JSON.parse(text) as SessionClientMessage;
    this.received.push(message);
    if (this.defer) this.queue.push(message);
    else this.apply(message);
  }

  /** The backend gets to the messages that waited. */
  flush() {
    this.defer = false;
    this.queue.splice(0).forEach((message) => this.apply(message));
  }

  /** Delivers a message to the client. */
  emit(message: SessionServerMessage) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  /** The network drops: the seat stays for its grace period. */
  drop(code = 1006, reason = "") {
    this.readyState = 3;
    if (this.seat?.socket === this) this.seat.socket = null;
    this.onclose?.({ code, reason });
  }

  /** The client side ends the connection. */
  close() {
    this.readyState = 3;
    if (this.seat?.socket === this) this.seat.socket = null;
  }

  /** Tells the client its session is over, and closes. */
  endWith(code: number, reason?: "stopped" | "ownerGone" | "replaced") {
    if (reason) this.emit({ type: "ended", reason });
    this.close();
    this.onclose?.({ code, reason: "" });
  }

  /** Another participant of this socket's session moves their cursor. */
  cursor(participant: number, at: CursorPoint | null) {
    this.emit({ type: "cursor", participant, at });
  }

  /** Sends this seat the Doc, if it has not got this one, then its part, then the roster. */
  push() {
    const { room, seat } = this;
    if (!room || !seat) return;
    if (this.sentDoc !== room.docRev) {
      this.sentDoc = room.docRev;
      this.emit({
        type: "doc",
        docRev: room.docRev,
        doc: room.shown.doc(seat.owner),
      });
    }
    // Only the owner holds the link.
    const link = seat.owner ? `http://host/#join=${room.link}` : undefined;
    const access: SeatAccess = seat.owner ? "owner" : (room.access ?? "watch");
    this.emit({
      type: "seat",
      rev: room.docRev,
      docRev: room.docRev,
      presenterRev: room.presenterRev,
      presenterBy: room.presenterBy,
      access,
      can: seatAccess()[access],
      mode: seat.view ? "exploring" : "following",
      sharing: room.access ? { access: room.access, link } : undefined,
      view: seat.view ?? room.presenter,
      viewsDone: seat.viewsDone,
    });
    this.emit({
      type: "roster",
      participants: room.seats.map((other) => ({
        id: other.id,
        name: `Guest ${other.id}`,
        color: other.id - 1,
        role: other.owner ? "owner" : "guest",
        mode: other.view ? "exploring" : "following",
        present: other.socket !== null || other.owner,
        sharesCursor: true,
      })),
    });
  }

  private apply(message: SessionClientMessage) {
    const { room, seat } = this;
    if (!room || !seat) return this.admit(message);
    switch (message.type) {
      case "view": {
        if (message.seq <= seat.viewsDone) return;
        seat.viewsDone = message.seq;
        // A seat that may only watch has its view refused; the number is finished all the same.
        if (seat.owner) room.publish(message.view, seat.id);
        else if (room.access && room.access !== "watch") seat.view = message.view;
        room.share();
        break;
      }
      case "command":
        this.command(room, seat, message.seq, message.cmd);
        break;
      case "timeSync":
        this.emit({
          type: "timeSyncReply",
          id: message.id,
          clientUs: message.clientUs,
          serverUs: message.clientUs + FakeSocket.clockOffsetUs,
        });
        break;
      case "cursor":
        room.seats
          .filter((other) => other !== seat)
          .forEach((other) =>
            other.socket?.emit({ type: "cursor", participant: seat.id, at: message.at }),
          );
        break;
      case "leave":
        this.close();
        room.seats.splice(room.seats.indexOf(seat), 1);
        if (seat.owner && !room.seats.some((other) => other.owner))
          room.seats.forEach((other) => other.socket?.endWith(4404, "stopped"));
        else room.share();
        break;
      case "hello":
      case "cursorOptions":
        break;
    }
  }

  private command(
    room: Room,
    seat: Seat,
    seq: number,
    cmd: Extract<SessionClientMessage, { type: "command" }>["cmd"],
  ) {
    const refuse = () =>
      this.emit({ type: "ack", seq, outcome: { status: "refused", code: "NotAllowed" } });
    if (cmd.type === "releaseStreams")
      return this.emit({ type: "ack", seq, outcome: { status: "ok" } });
    if (cmd.type === "requestTicket")
      return FakeSocket.refuseTickets
        ? refuse()
        : this.emit({
            type: "ticket",
            seq,
            ticket: `ticket-${++FakeSocket.tokens}`,
            expiresMs: 10_000,
          });
    if (cmd.type === "follow") seat.view = null;
    else if (cmd.type === "setOverlay") {
      // As for a viewport: refused for a Guest that may only watch, applied to the view the seat shows now.
      if (!seat.owner && room.access !== "explore") return refuse();
      const base = seat.view ?? room.presenter;
      const view = { ...base, overlays: { ...base.overlays, [cmd.overlay]: cmd.shown } };
      if (seat.owner) room.publish(view, seat.id);
      else seat.view = view;
    } else if (cmd.type === "setViewport") {
      // A Guest that may only watch is refused; one that follows starts exploring with a copy of the presenter's.
      if (!seat.owner && room.access !== "explore") return refuse();
      const base = seat.view ?? room.presenter;
      const shown = base.viewers[cmd.viewer];
      const viewers = shown
        ? { ...base.viewers, [cmd.viewer]: { ...shown, viewport: cmd.viewport } }
        : base.viewers;
      if (seat.owner) room.publish({ ...base, viewers }, seat.id);
      else seat.view = { ...base, viewers };
    } else if (cmd.type === "control") {
      if (!seat.owner) return refuse();
      const control = cmd.control;
      if (control.type === "share" || control.type === "setAccess") room.allow(control.access);
      else if (control.type === "stop") {
        room.access = null;
        const guests = room.seats.filter((other) => !other.owner);
        guests.forEach((other) => other.socket?.endWith(4404, "stopped"));
        guests.forEach((other) => room.seats.splice(room.seats.indexOf(other), 1));
      } else room.secret += "x";
    } else if (cmd.type === "workspace") {
      if (!seat.owner) return refuse();
      const { command: change } = cmd;
      if (
        change.type === "save" &&
        change.path &&
        !change.replace &&
        FakeSocket.existingFiles.has(change.path)
      )
        return this.emit({
          type: "ack",
          seq,
          outcome: { status: "failed", error: { code: "WorkspaceExists", params: {} } },
        });
      this.change(room, cmd);
    } else {
      if (!seat.owner) return refuse();
      if (FakeSocket.refuseContent)
        return this.emit({
          type: "ack",
          seq,
          outcome: { status: "refused", code: FakeSocket.refuseContent },
        });
      if (FakeSocket.failContent)
        return this.emit({
          type: "ack",
          seq,
          outcome: { status: "failed", error: FakeSocket.failContent },
        });
      this.change(room, cmd);
    }
    this.emit({ type: "ack", seq, outcome: { status: "ok" } });
    room.share();
  }

  /** Moves the room to the state the backend recorded for `cmd`; a command with none is noted, unless exempt. */
  private change(room: Room, cmd: Extract<SessionCommand, { type: "content" | "workspace" }>) {
    if (room.shown.step(cmd)) room.docRev += 1;
    else if (!UNRECORDED.has(nameOf(cmd)))
      FakeSocket.unrecorded.push(
        `${JSON.stringify(cmd)} in ${room.shown.scenario.name}/${room.shown.state}`,
      );
  }

  /** Seats the socket by its `Hello`, or closes it with the code the real backend gives. */
  private admit(message: SessionClientMessage) {
    if (message.type !== "hello") return this.endWith(1007);
    const { auth, resume } = message;
    if (!resume && auth.type !== "join" && FakeSocket.serverPassword !== null) {
      // Not allowed without a password, invalid with a wrong one, as the real backend closes.
      if (auth.serverPassword === undefined) return this.endWith(1008);
      if (auth.serverPassword !== FakeSocket.serverPassword) return this.endWith(4401);
    }
    let room: Room | undefined;
    let seat: Seat | undefined;
    if (resume) {
      room = [...FakeSocket.rooms.values()].find((candidate) =>
        candidate.seats.some((held) => held.resume === resume),
      );
      seat = room?.seats.find((held) => held.resume === resume);
      // The newest socket takes the seat; the old one is told so.
      seat?.socket?.endWith(4409, "replaced");
    } else if (auth.type === "own") {
      room = new Room(`room-${FakeSocket.rooms.size + 1}`);
      FakeSocket.rooms.set(room.id, room);
      seat = room.seat(this, true);
    } else {
      const id = auth.type === "attach" ? auth.session : auth.link.split(".")[0];
      room = FakeSocket.rooms.get(id ?? "");
      if (auth.type === "attach") {
        if (!room) return this.endWith(4404);
        seat = room.seat(this, true);
      } else {
        if (!room?.access) return this.endWith(4404);
        if (auth.link !== room.link) return this.endWith(4401);
        seat = room.seat(this, false);
      }
    }
    if (!room || !seat) return this.endWith(4404);
    seat.resume = `${room.id}-${seat.id}-${++FakeSocket.tokens}`;
    seat.socket = this;
    [this.room, this.seat] = [room, seat];
    this.emit({
      type: "welcome",
      session: room.id,
      seat: seat.id,
      resume: seat.resume,
      viewsDone: seat.viewsDone,
      serverUs: FakeSocket.clockOffsetUs,
    });
    room.share();
  }
}
