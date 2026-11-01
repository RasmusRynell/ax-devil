import type {
  Access,
  Can,
  CursorPoint,
  ItemState,
  ResolutionState,
  SeatAccess,
  SessionClientMessage,
  SessionServerMessage,
  View,
} from "../generated/api";
import { EMPTY_VIEW } from "./views";

export const resolved = (id: string): ResolutionState => ({
  status: "Resolved",
  content: { kind: "SeekableVideo", id: `content-${id}`, generation: 1, display_name: `${id}.mp4` },
});

export const video = (id: string, open = true, resolution = resolved(id)): ItemState => ({
  id,
  label: `${id}.mp4`,
  open,
  resolution,
});

/** What the fake tells each kind of seat it can do. Fixed test data, not policy: the real backend decides. */
const CAN: Record<SeatAccess, Can> = {
  owner: { view: true, content: true, host: true, control: true },
  watch: { view: false, content: false, host: false, control: false },
  explore: { view: true, content: false, host: false, control: false },
  edit: { view: true, content: true, host: false, control: false },
};

interface Seat {
  id: number;
  owner: boolean;
  /** The seat's own view while it explores; null while it follows. */
  view: View | null;
  viewsDone: number;
  resume: string;
  socket: FakeSocket | null;
}

/** `view` cut down to the viewers the Doc has open. */
function restrict(view: View, items: readonly ItemState[]): View {
  const open = new Set(items.filter((item) => item.open).map((item) => item.id));
  return {
    sidebar: view.sidebar,
    active: view.active && open.has(view.active) ? view.active : undefined,
    viewers: Object.fromEntries(Object.entries(view.viewers).filter(([id]) => open.has(id))),
  };
}

/**
 * One Session of the fake backend, as the real one keeps it: the Doc, the presenter's view, sharing, and a seat per
 * participant with its own view while it explores. Tests drive the owner through it when the client under test is a
 * watcher.
 */
export class Room {
  items: ItemState[] = [];
  docRev = 0;
  presenter: View = EMPTY_VIEW;
  /** What watchers may do while sharing is on; null while it is off. */
  access: Access | null = null;
  secret = "s3cret";
  readonly seats: Seat[] = [];
  constructor(readonly id: string) {}

  /** What a join link for this session holds. */
  get link() {
    return `${this.id}.${this.secret}`;
  }

  /** Adds a participant; the owner is the first. */
  seat(socket: FakeSocket | null): Seat {
    const id = this.seats.length + 1;
    const owner = id === 1;
    const view = !owner && this.access === "edit" ? this.presenter : null;
    const seat: Seat = { id, owner, view, viewsDone: 0, resume: "", socket };
    this.seats.push(seat);
    return seat;
  }

  /** The owner shows another view. */
  publish(view: View) {
    this.presenter = restrict(view, this.items);
    this.share();
  }

  /** The Doc changes. */
  setItems(items: ItemState[]) {
    this.items = items;
    this.docRev += 1;
    this.presenter = restrict(this.presenter, items);
    this.seats.forEach((seat) => seat.view && (seat.view = restrict(seat.view, items)));
    this.share();
  }

  /** The owner changes what watchers may do; a watcher that may not explore goes back to following. */
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
  /** The backend's clock minus the client's, in microseconds. */
  static clockOffsetUs = 5_000_000;
  /** While true, the backend refuses every stream ticket, as it does for content the Doc no longer lets the seat play. */
  static refuseTickets = false;
  /** The server password the fake backend asks of a caller who is not joining by a link; null for a server without one. */
  static serverPassword: string | null = null;

  static reset() {
    FakeSocket.refuseTickets = false;
    FakeSocket.serverPassword = null;
    FakeSocket.sockets = [];
    FakeSocket.rooms.clear();
    FakeSocket.clockOffsetUs = 5_000_000;
  }

  /** A session someone else owns and shares, for the client under test to watch. */
  static host(access: Access = "watch"): Room {
    const room = new Room(`room-${FakeSocket.rooms.size + 1}`);
    room.access = access;
    room.seat(null);
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
        doc: { workspace: { name: null, items: room.items } },
      });
    }
    // Only the owner holds the link.
    const link = seat.owner ? `http://host/#join=${room.link}` : undefined;
    const access: SeatAccess = seat.owner ? "owner" : (room.access ?? "watch");
    this.emit({
      type: "seat",
      rev: room.docRev,
      docRev: room.docRev,
      presenterRev: 0,
      access,
      can: CAN[access],
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
        role: other.owner ? "owner" : "watcher",
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
        if (seat.owner) room.presenter = restrict(message.view, room.items);
        else if (room.access && room.access !== "watch")
          seat.view = restrict(message.view, room.items);
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
        if (seat.owner) room.seats.forEach((other) => other.socket?.endWith(4404, "stopped"));
        else {
          room.seats.splice(room.seats.indexOf(seat), 1);
          room.share();
        }
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
    else if (cmd.type === "control") {
      if (!seat.owner) return refuse();
      const control = cmd.control;
      if (control.type === "share" || control.type === "setAccess") room.allow(control.access);
      else if (control.type === "stop") {
        room.access = null;
        room.seats.slice(1).forEach((other) => other.socket?.endWith(4404, "stopped"));
        room.seats.splice(1);
      } else room.secret += "x";
    } else {
      const edits = seat.owner || room.access === "edit";
      if (!edits) return refuse();
      const doc = cmd.command;
      if (doc.type === "addItems") {
        const added = doc.items.map((_, at) => String.fromCharCode(97 + room.items.length + at));
        room.setItems([...room.items, ...added.map((id) => video(id, doc.open))]);
      } else {
        const open = doc.type === "openViewer";
        room.setItems(room.items.map((item) => (item.id === doc.item ? { ...item, open } : item)));
      }
    }
    this.emit({ type: "ack", seq, outcome: { status: "ok" } });
    room.share();
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
      seat = room.seat(this);
    } else {
      const id = auth.type === "watch" ? auth.session : auth.link.split(".")[0];
      room = FakeSocket.rooms.get(id ?? "");
      if (!room?.access) return this.endWith(4404);
      if (auth.type === "join" && auth.link !== room.link) return this.endWith(4401);
      seat = room.seat(this);
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
