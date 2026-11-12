import { CommandRefused } from "../client/errors";
import type { ClientMessage } from "../stream/protocol";
import { RETRY_MS, type StreamDeps } from "./streamDeps";
import type { Connection, Incoming } from "../stream/wire";

type Open = Extract<ClientMessage, { type: "Open" }>;

/** What a `Link` tells the stream. */
export interface LinkHandlers {
  /** The Open to send on a new connection, without its ticket. */
  open: () => Omit<Open, "ticket">;
  incoming: (incoming: Incoming) => void;
  /** The backend refused a ticket for good. */
  refused: (reason: string) => void;
  /** No ticket could be had, as the session's own connection is down: worth another try. */
  lost: () => void;
}

/** One viewer's socket to the backend: the ticket each connection spends, the Open it starts with, and the retries. */
export class Link {
  readonly #deps: Pick<StreamDeps, "ticket" | "connect" | "after">;
  readonly #handlers: LinkHandlers;
  #connection: Connection | undefined;
  /** Names this connection, so a late message of an earlier one is ignored. */
  #attempt: object = {};
  #retries = 0;
  #cancelRetry: (() => void) | undefined;
  #closed = false;

  constructor(deps: Pick<StreamDeps, "ticket" | "connect" | "after">, handlers: LinkHandlers) {
    this.#deps = deps;
    this.#handlers = handlers;
  }

  /** Gets a ticket, connects, and sends the Open. */
  connect(): void {
    const attempt = {};
    this.#attempt = attempt;
    const current = () => this.#attempt === attempt && !this.#closed;
    this.#deps.ticket().then(
      (ticket) => {
        if (!current()) return;
        this.#connection = this.#deps.connect((incoming) => {
          if (current()) this.#handlers.incoming(incoming);
        });
        this.#connection.send({ ...this.#handlers.open(), ticket });
      },
      (error: unknown) => {
        if (!current()) return;
        if (error instanceof CommandRefused) this.#handlers.refused(error.message);
        else this.#handlers.lost();
      },
    );
  }

  send(message: ClientMessage): void {
    this.#connection?.send(message);
  }

  /** The stream is open: the next loss waits the shortest time again. */
  settled(): void {
    this.#retries = 0;
  }

  /** The socket closed on its own: forgets it and connects again after the next wait. */
  retry(): void {
    this.#connection = undefined;
    const wait = RETRY_MS[Math.min(this.#retries++, RETRY_MS.length - 1)];
    this.#cancelRetry = this.#deps.after(() => this.connect(), wait);
  }

  /** Closes the socket without telling the backend, which already ended the stream. */
  drop(): void {
    this.#connection?.close();
    this.#connection = undefined;
  }

  /** Ends the stream: tells the backend, closes the socket and ignores whatever comes later. */
  close(): void {
    this.#closed = true;
    this.send({ type: "Close" });
    this.drop();
    this.#cancelRetry?.();
  }
}
