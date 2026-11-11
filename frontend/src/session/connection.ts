import type {
  CursorOptions,
  SessionAuth,
  SessionClientMessage,
  SessionServerMessage,
} from "../generated/api";

/** How a client asks to be seated the first time. */
export type Entry =
  | { kind: "own" }
  | { kind: "attach"; session: string }
  /** `link` is the join link's `<id>.<secret>`. */
  | { kind: "join"; link: string };

/**
 * Why a connection is over for good. The first three are what the session itself says; the rest are what the
 * server answered with, or that the session cannot be found any more.
 */
export type Ending =
  | "stopped"
  | "ownerGone"
  | "replaced"
  | "unknown"
  | "invalid"
  | "limited"
  | "full"
  | "refused";

/** Where a connection is: waiting for its seat, seated, seated but offline, or over. */
export type Status =
  | { state: "connecting" | "online" | "reconnecting" }
  | { state: "ended"; why: Ending; detail?: string };

export interface ConnectionOptions {
  /** How to be seated, or null to only take back `resume`. */
  entry: Entry | null;
  /** A seat to take back first, such as the one a reload left behind; `entry` is the fallback when it is gone. */
  resume?: string;
  /** The server password, for a server that has one. */
  password?: string;
  /** The cursor choices to seat with; read again on every reconnect. */
  cursors: () => CursorOptions;
  onMessage: (message: SessionServerMessage) => void;
  onStatus: (status: Status) => void;
}

export interface Connection {
  /** Sends while the socket is open and reports whether it did; a message sent while offline is dropped. */
  send: (message: SessionClientMessage) => boolean;
  /** Ends the connection without telling the session; the seat stays for its grace period. */
  close: () => void;
}

/** Without a message from the server for this long (it sends one every 10 s), the link is dead. */
export const SILENCE_MS = 25_000;
const BACKOFF_MS = [500, 1000, 2000, 5000];

/** The final close codes; anything else is a lost connection that is worth another try. */
const TERMINAL: Record<number, Ending> = {
  1007: "refused",
  1008: "refused",
  1009: "refused",
  1013: "full",
  4401: "invalid",
  4404: "unknown",
  4409: "replaced",
  4429: "limited",
};

function socketUrl(): string {
  const url = new URL("/api/session", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.href;
}

function authOf(entry: Entry | null, serverPassword?: string): SessionAuth {
  // A resume needs no way in: the token alone is the credential.
  if (!entry || entry.kind === "own") return { type: "own", serverPassword };
  if (entry.kind === "attach") return { type: "attach", session: entry.session, serverPassword };
  return { type: "join", link: entry.link };
}

/**
 * Opens the Session socket and keeps it: `Hello` first, then `Hello` with the token each `Welcome` brings after
 * every drop, with growing pauses. A server that says nothing for [`SILENCE_MS`] counts as gone. Close codes 4401,
 * 4404, 4409, 4429 and 1013 and a session's `Ended` message are final and reported once through `onStatus`.
 */
export function connect(options: ConnectionOptions): Connection {
  let socket: WebSocket | null = null;
  let token = options.resume;
  let seated = false;
  let over = false;
  let failures = 0;
  let silence: ReturnType<typeof setTimeout> | undefined;
  let pause: ReturnType<typeof setTimeout> | undefined;

  const finish = (why: Ending, detail?: string) => {
    if (over) return;
    over = true;
    clearTimeout(silence);
    clearTimeout(pause);
    socket?.close();
    options.onStatus({ state: "ended", why, detail });
  };

  const lost = () => {
    clearTimeout(silence);
    socket = null;
    options.onStatus({ state: seated ? "reconnecting" : "connecting" });
    pause = setTimeout(open, BACKOFF_MS[Math.min(failures, BACKOFF_MS.length - 1)]);
    failures += 1;
  };

  function open() {
    const current = new WebSocket(socketUrl());
    socket = current;
    const resuming = token !== undefined;
    const listen = () => {
      clearTimeout(silence);
      silence = setTimeout(() => {
        if (socket !== current) return;
        current.close();
        lost();
      }, SILENCE_MS);
    };
    current.onopen = () => {
      const hello: SessionClientMessage = {
        type: "hello",
        auth: authOf(options.entry, options.password),
        resume: token,
        cursors: options.cursors(),
      };
      current.send(JSON.stringify(hello));
      listen();
    };
    current.onmessage = (event: MessageEvent<string>) => {
      if (socket !== current || over) return;
      listen();
      const message = JSON.parse(event.data) as SessionServerMessage;
      if (message.type === "welcome") {
        token = message.resume;
        seated = true;
        failures = 0;
        options.onStatus({ state: "online" });
        options.onMessage(message);
      } else if (message.type === "ended") finish(message.reason);
      else options.onMessage(message);
    };
    current.onclose = (event: CloseEvent) => {
      if (socket !== current || over) return;
      // A seat the server no longer holds: start over with the way in, once.
      if (event.code === 4404 && resuming && !seated && options.entry) {
        token = undefined;
        socket = null;
        open();
        return;
      }
      const why = TERMINAL[event.code];
      if (why) finish(why, event.reason || undefined);
      else lost();
    };
  }

  open();
  return {
    send: (message) => {
      if (socket?.readyState !== WebSocket.OPEN) return false;
      socket.send(JSON.stringify(message));
      return true;
    },
    close: () => {
      over = true;
      clearTimeout(silence);
      clearTimeout(pause);
      socket?.close();
      socket = null;
    },
  };
}
