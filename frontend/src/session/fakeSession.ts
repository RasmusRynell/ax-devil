import type {
  Access,
  Can,
  CursorPoint,
  EntryInfo,
  ErrorWire,
  HiddenTarget,
  ItemState,
  RefusalCode,
  ResolutionState,
  SeatAccess,
  SessionClientMessage,
  SessionServerMessage,
  View,
} from "../generated/api";
import { hides, rowsOf } from "./fakeRows";
import { EMPTY_VIEW } from "./views";

export const resolved = (id: string): ResolutionState => ({
  status: "Resolved",
  content: {
    kind: "SeekableVideo",
    id: `content-${id}`,
    generation: 1,
    display_name: `${id}.mp4`,
    entry: { id: `content-${id}`, label: `${id}.mp4`, lanes: [`${id}.mp4`] },
  },
});

/** One entry of a playlist: its id, and the lanes it plays. */
export const entry = (id: string, label = id, lanes: string[] = [label]): EntryInfo => ({
  id,
  label,
  lanes,
});

/** A playlist item that resolved to these entries. */
export const playlist = (id: string, entries: EntryInfo[], open = true): ItemState =>
  playlists(id, [[id, entries]], open);

/** A playlist item that resolved to several named playlists, their entries in order. */
export const playlists = (
  id: string,
  groups: readonly (readonly [name: string, entries: EntryInfo[]])[],
  open = true,
): ItemState => ({
  id,
  label: id,
  kind: "playlist",
  open,
  resolution: {
    status: "Resolved",
    content: {
      kind: "Playlist",
      id,
      generation: 1,
      display_name: id,
      entries: groups.flatMap(([, entries]) => entries),
      playlists: groups.map(([name, entries]) => ({
        id: `${id}/${name}`,
        label: groups.length > 1 ? `${id} / ${name}` : id,
        entries: entries.length,
      })),
    },
  },
});

/** An item that failed; `reason` is what a seat with host details also reads. */
export const failed = (id: string, reason?: string): ItemState => ({
  id,
  label: `${id}.mp4`,
  kind: "video",
  open: false,
  resolution: { status: "Failed", code: "FileNotFound", params: {}, reason },
});

export const video = (id: string, open = true, resolution = resolved(id)): ItemState => ({
  id,
  label: `${id}.mp4`,
  kind: "video",
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

const entriesOf = ({ resolution }: ItemState): EntryInfo[] => {
  if (resolution.status !== "Resolved") return [];
  const { content } = resolution;
  if (content.kind === "Playlist") return content.entries;
  return content.kind === "SeekableVideo" ? [content.entry] : [];
};

/**
 * `view` cut down to the viewers the Doc has open, each naming an entry: the one it asked for, else the first of its
 * item's Content that is not hidden, else the first. The backend also swaps an entry the Content does not have; the
 * fake leaves it, which lets a test name an entry of a Content it has not built.
 */
function restrict(view: View, items: readonly ItemState[], hidden: readonly HiddenTarget[]): View {
  const open = new Map(items.filter((item) => item.open).map((item) => [item.id, item]));
  const viewers: View["viewers"] = {};
  for (const [id, viewer] of Object.entries(view.viewers)) {
    const item = open.get(id);
    if (!item || !viewer) continue;
    const entries = entriesOf(item);
    const fallback = entries.find(({ id: entryId }) => !hides(hidden, entryId)) ?? entries[0];
    viewers[id] = { ...viewer, entry: viewer.entry ?? fallback?.id };
  }
  return {
    sidebar: view.sidebar,
    active: view.active && open.has(view.active) ? view.active : undefined,
    viewers,
  };
}

/**
 * One Session of the fake backend, as the real one keeps it: the Doc, the presenter's view, sharing, and a seat per
 * participant with its own view while it explores. Tests drive the owner through it when the client under test is a
 * watcher.
 */
export class Room {
  items: ItemState[] = [];
  /** What the owner hid in the workspace tree. */
  hidden: HiddenTarget[] = [];
  docRev = 0;
  /** Whether changes restored from a recovery copy await Keep or Discard. */
  recovered = false;
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
    this.presenter = restrict(view, this.items, this.hidden);
    this.share();
  }

  /** The Doc changes. */
  setItems(items: ItemState[]) {
    this.items = items;
    this.docRev += 1;
    this.presenter = restrict(this.presenter, items, this.hidden);
    this.seats.forEach(
      (seat) => seat.view && (seat.view = restrict(seat.view, items, this.hidden)),
    );
    this.share();
  }

  /** The owner hides or shows an entry or one lane of it. */
  setHidden({ entry, lane }: HiddenTarget, hidden: boolean) {
    this.hidden = this.hidden.filter((target) => !hides([target], entry, lane));
    if (hidden) this.hidden.push({ entry, lane });
    this.setItems(this.items);
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

  static reset() {
    FakeSocket.refuseTickets = false;
    FakeSocket.existingFiles.clear();
    FakeSocket.refuseContent = null;
    FakeSocket.failContent = null;
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
        doc: {
          workspace: {
            name: null,
            modified: room.recovered,
            recovered: room.recovered,
            epoch: 0,
            file: null,
            items: room.items,
            rows: rowsOf(room.items, room.hidden),
          },
        },
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
        if (seat.owner) room.presenter = restrict(message.view, room.items, room.hidden);
        else if (room.access && room.access !== "watch")
          seat.view = restrict(message.view, room.items, room.hidden);
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
      if (cmd.command.type === "keep" || cmd.command.type === "discard") {
        room.recovered = false;
        room.setItems(room.items);
      }
    } else {
      const edits = seat.owner || room.access === "edit";
      if (!edits) return refuse();
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
      const doc = cmd.command;
      if (doc.type === "addItems") {
        const added = doc.items.map((item, at): ItemState => {
          const id = String.fromCharCode(97 + room.items.length + at);
          // A live stream is added now and resolves later, as the backend does it.
          return item.type === "Video"
            ? video(id, doc.open)
            : {
                id,
                label: item.label ?? "Live stream",
                kind: "live",
                open: doc.open,
                resolution: { status: "Pending" },
              };
        });
        room.setItems([...room.items, ...added]);
      } else if (doc.type === "addResolved") {
        const id = String.fromCharCode(97 + room.items.length);
        room.setItems([
          ...room.items,
          {
            id,
            label: doc.label ?? "Playlist",
            kind: "playlist",
            open: true,
            resolution: { status: "Pending" },
          },
        ]);
      } else if (doc.type === "setHidden") room.setHidden(doc.target, doc.hidden);
      else if (doc.type === "remove") room.setItems(room.items.filter(({ id }) => id !== doc.item));
      else if (doc.type !== "setLaneMatching") {
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
