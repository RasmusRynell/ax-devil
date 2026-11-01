import type {
  CursorPoint,
  ShareClientMessage,
  ShareServerMessage,
  ShareSnapshot,
  ViewState,
} from "../generated/api";

interface Seat {
  id: number;
  role: "owner" | "watcher";
  /** The seat's own view while it explores; null while it follows. */
  view: ViewState | null;
  viewSeq: number;
  resume: string;
  socket: FakeSocket | null;
}

const WORKSPACE: ViewState = { sidebarView: "workspace", sidebarOpen: true };

/**
 * One session of the fake backend, as the real one keeps it: the presenter's view, the owner's options, and a seat
 * per participant with its own view while it explores. Tests drive the owner through it when the client under test
 * is a watcher.
 */
export class Room {
  presenter = WORKSPACE;
  explore = false;
  secret = "s3cret";
  /** True while a test plays the owner without a socket. */
  hosted = false;
  private revision = 0;
  readonly seats: Seat[] = [];
  constructor(readonly id: string) {}

  /** What a join link for this session holds. */
  get link() {
    return `${this.id}.${this.secret}`;
  }

  /** Adds a participant; the owner is the first. */
  seat(socket: FakeSocket | null): Seat {
    const id = this.seats.length + 1;
    const seat: Seat = {
      id,
      role: id === 1 ? "owner" : "watcher",
      view: null,
      viewSeq: 0,
      resume: `${this.id}-${id}-${++FakeSocket.tokens}`,
      socket,
    };
    this.seats.push(seat);
    return seat;
  }

  /** The owner shows another view. */
  publish(view: ViewState) {
    this.presenter = view;
    this.share();
  }

  /** The owner allows or forbids exploring; forbidding moves every explorer back to following. */
  allowExplore(allowed: boolean) {
    this.explore = allowed;
    if (!allowed) this.seats.forEach((seat) => (seat.view = null));
    this.share();
  }

  /** The session ends for everyone still in it. */
  end(reason: "stopped" | "ownerGone") {
    this.seats.forEach((seat) => seat.socket?.endWith(4404, reason));
    FakeSocket.rooms.delete(this.id);
  }

  /** Sends every connected seat its own snapshot. */
  share() {
    this.revision += 1;
    for (const seat of this.seats)
      seat.socket?.emit({ type: "snapshot", ...this.snapshotFor(seat) });
  }

  private present(seat?: Seat) {
    return seat?.socket != null || (this.hosted && seat?.role === "owner");
  }

  snapshotFor(seat: Seat): ShareSnapshot {
    return {
      revision: this.revision,
      name: "Guest 1",
      owner: 1,
      ownerPresent: this.present(this.seats[0]),
      options: { explore: this.explore },
      participants: this.seats.map((other) => ({
        id: other.id,
        name: `Guest ${other.id}`,
        color: other.id - 1,
        role: other.role,
        mode: other.view ? "exploring" : "following",
        present: this.present(other),
        sharesCursor: true,
      })),
      presenter: this.presenter,
      mode: seat.view ? "exploring" : "following",
      view: seat.view ?? this.presenter,
      viewSeq: seat.viewSeq,
    };
  }
}

/**
 * Stands in for `WebSocket` and the backend behind it: seats a `Hello`, answers with `Welcome` and a snapshot of its
 * own per seat, applies what each role may send and closes with the codes the real socket uses. Install it with
 * `vi.stubGlobal("WebSocket", FakeSocket)` and call `FakeSocket.reset()` between tests.
 */
export class FakeSocket {
  static readonly OPEN = 1;
  static sockets: FakeSocket[] = [];
  static rooms = new Map<string, Room>();
  static tokens = 0;

  static reset() {
    FakeSocket.sockets = [];
    FakeSocket.rooms.clear();
  }

  /** A session someone else owns, for the client under test to watch. */
  static host(): Room {
    const room = new Room(`room-${FakeSocket.rooms.size + 1}`);
    room.hosted = true;
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
  readonly received: ShareClientMessage[] = [];
  /** While true, what the client sends waits for `flush()`: the backend has not got to it yet. */
  defer = false;
  private queue: ShareClientMessage[] = [];
  private room: Room | null = null;
  private seat: Seat | null = null;

  constructor(readonly url: string) {
    FakeSocket.sockets.push(this);
    queueMicrotask(() => {
      this.readyState = FakeSocket.OPEN;
      this.onopen?.();
    });
  }

  /** What the client sends. */
  send(text: string) {
    const message = JSON.parse(text) as ShareClientMessage;
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
  emit(message: ShareServerMessage) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  /** The network drops: the seat stays for its grace period. */
  drop(code = 1006, reason = "") {
    this.readyState = 3;
    if (this.seat?.socket === this) this.seat.socket = null;
    this.room?.share();
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

  private apply(message: ShareClientMessage) {
    const { room, seat } = this;
    if (!room || !seat) return this.admit(message);
    const owner = seat.role === "owner";
    switch (message.type) {
      case "publish":
      case "setOptions":
      case "regenerateLink":
        if (!owner) return this.endWith(1008);
        if (message.type === "publish") room.publish(message.state);
        else if (message.type === "setOptions") room.allowExplore(message.explore);
        else {
          room.secret = `${room.secret}x`;
          this.emit({ type: "link", url: `http://host/#join=${room.link}` });
        }
        break;
      case "explore":
      case "setView":
      case "follow":
        if (owner) return this.endWith(1008);
        if (message.type === "explore" && room.explore) seat.view ??= room.presenter;
        else if (message.type === "setView" && seat.view) {
          // As the real backend: a seq older than the latest accepted is ignored.
          if (message.seq >= seat.viewSeq) {
            seat.view = message.state;
            seat.viewSeq = message.seq;
          }
        } else if (message.type === "follow") seat.view = null;
        room.share();
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
        if (owner) room.end("stopped");
        else {
          room.seats.splice(room.seats.indexOf(seat), 1);
          room.share();
        }
        break;
    }
  }

  /** Seats the socket by its `Hello`, or closes it with the code the real backend gives. */
  private admit(message: ShareClientMessage) {
    if (message.type !== "hello") return this.endWith(1007);
    const { access } = message;
    let room: Room | undefined;
    let seat: Seat | undefined;
    if (access.type === "create") {
      room = new Room(`room-${FakeSocket.rooms.size + 1}`);
      FakeSocket.rooms.set(room.id, room);
      seat = room.seat(this);
    } else if (access.type === "resume") {
      room = [...FakeSocket.rooms.values()].find((candidate) =>
        candidate.seats.some((held) => held.resume === access.token),
      );
      seat = room?.seats.find((held) => held.resume === access.token);
      // The newest socket takes the seat; the old one is told so.
      seat?.socket?.endWith(4409, "replaced");
    } else {
      const id = access.type === "watch" ? access.session : access.link.split(".")[0];
      room = FakeSocket.rooms.get(id ?? "");
      if (!room) return this.endWith(4404);
      if (access.type === "join" && access.link !== room.link) return this.endWith(4401);
      seat = room.seat(this);
    }
    if (!room || !seat) return this.endWith(4404);
    seat.resume = `${seat.resume}+`;
    seat.socket = this;
    [this.room, this.seat] = [room, seat];
    this.emit({
      type: "welcome",
      session: room.id,
      you: seat.id,
      role: seat.role,
      resume: seat.resume,
      link: seat.role === "owner" ? `http://host/#join=${room.link}` : null,
    });
    room.share();
  }
}
