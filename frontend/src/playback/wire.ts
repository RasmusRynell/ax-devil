/** What the playback socket delivers to the session, parsed where it was received. */

import {
  EnvelopeError,
  parseEnvelope,
  type ClientMessage,
  type Envelope,
  type JpegFrame,
  type ServerMessage,
} from "./protocol";
import type { Window } from "./FrameBuffer";

export type Incoming =
  | { type: "message"; message: ServerMessage }
  | { type: "envelope"; envelope: Envelope }
  /** A message that breaks the protocol; the session ends. */
  | { type: "malformed"; reason: string }
  /** The socket closed, however it ended; `code` is its close code, 1006 when it dropped without one. */
  | { type: "closed"; code: number };

/** What one socket message holds: what the session handles, or a JPEG frame the socket's thread decodes first. */
export type Received = Incoming | { type: "jpeg"; frame: JpegFrame };

/** One socket to the backend. Messages sent before it is open are held until it is. */
export interface Connection {
  send(message: ClientMessage): void;
  close(): void;
}

/** Opens a socket and reports everything it receives, in order. */
export type Connect = (onIncoming: (incoming: Incoming) => void) => Connection;

/**
 * What the socket's thread received and has not yet handed on, in order, decoding one JPEG frame at a time. A live
 * stream shows only its newest frame, so a frame still waiting is dropped when a newer one arrives. An offline frame
 * is kept while it is inside the window of the newest `Want`, the rule the session's `FrameBuffer` applies too, so a
 * seek never waits behind decoding frames it would throw away. Once the socket closes, waiting frames are dropped:
 * they were for a stream that is over.
 */
export class Inbox {
  #waiting: Received[] = [];
  #draining = false;
  #live = false;
  #window: Window | undefined;

  constructor(
    private readonly settle: (received: Received) => Promise<Incoming>,
    private readonly deliver: (incoming: Incoming) => void,
  ) {}

  /** Queues `received` behind everything received before it. */
  push(received: Received): void {
    if (received.type === "message" && received.message.type === "LiveOpened") this.#live = true;
    if ((received.type === "jpeg" && this.#live) || received.type === "closed")
      this.#waiting = this.#waiting.filter((waiting) => waiting.type !== "jpeg");
    if (received.type === "jpeg" && !this.#wanted(received.frame)) return;
    this.#waiting.push(received);
    void this.#drain();
  }

  /** Records the window of a `Want` the viewer sent and drops the waiting frames outside it. */
  want(window: Window): void {
    this.#window = window;
    this.#waiting = this.#waiting.filter(
      (waiting) => waiting.type !== "jpeg" || this.#wanted(waiting.frame),
    );
  }

  #wanted({ sequenceId }: JpegFrame): boolean {
    if (!this.#window) return true;
    const { playhead, ahead, behind } = this.#window;
    return sequenceId >= playhead - behind && sequenceId <= playhead + ahead;
  }

  async #drain(): Promise<void> {
    if (this.#draining) return;
    this.#draining = true;
    for (let next = this.#waiting.shift(); next; next = this.#waiting.shift())
      this.deliver(await this.settle(next));
    this.#draining = false;
  }
}

/** Turns one received socket message into what the session handles; `null` for kinds this client ignores. */
export function decode(data: string | ArrayBuffer): Received | null {
  try {
    if (typeof data === "string")
      return { type: "message", message: JSON.parse(data) as ServerMessage };
    const envelope = parseEnvelope(data);
    if (envelope?.kind === "jpeg") return { type: "jpeg", frame: envelope.frame };
    return envelope ? { type: "envelope", envelope } : null;
  } catch (error) {
    if (error instanceof EnvelopeError || error instanceof SyntaxError)
      return { type: "malformed", reason: error.message };
    throw error;
  }
}

/** The ArrayBuffer behind an envelope, which a worker transfers to the main thread instead of copying. */
export function bufferOf(envelope: Envelope): ArrayBufferLike {
  switch (envelope.kind) {
    case "frame":
      return envelope.frame.planes[0].buffer;
    case "timeline":
      return envelope.times.buffer;
    case "sample":
      return envelope.item.buffer;
    case "map":
      return envelope.samples.buffer;
  }
}
