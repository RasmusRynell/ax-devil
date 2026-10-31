/** What the playback socket delivers to the session, parsed where it was received. */

import {
  EnvelopeError,
  parseEnvelope,
  type ClientMessage,
  type Envelope,
  type ServerMessage,
} from "./protocol";

export type Incoming =
  | { type: "message"; message: ServerMessage }
  | { type: "envelope"; envelope: Envelope }
  /** A message that breaks the protocol; the session ends. */
  | { type: "malformed"; reason: string }
  /** The socket closed, however it ended. */
  | { type: "closed" };

/** One socket to the backend. Messages sent before it is open are held until it is. */
export interface Connection {
  send(message: ClientMessage): void;
  close(): void;
}

/** Opens a socket and reports everything it receives, in order. */
export type Connect = (onIncoming: (incoming: Incoming) => void) => Connection;

/** Turns one received socket message into what the session handles; `null` for kinds this client ignores. */
export function decode(data: string | ArrayBuffer): Incoming | null {
  try {
    if (typeof data === "string")
      return { type: "message", message: JSON.parse(data) as ServerMessage };
    const envelope = parseEnvelope(data);
    return envelope && { type: "envelope", envelope };
  } catch (error) {
    if (error instanceof EnvelopeError || error instanceof SyntaxError)
      return { type: "malformed", reason: error.message };
    throw error;
  }
}

/** The ArrayBuffer behind an envelope, which a worker transfers to the main thread instead of copying. */
export function bufferOf(envelope: Envelope): ArrayBufferLike {
  return envelope.kind === "frame" ? envelope.frame.planes[0].buffer : envelope.times.buffer;
}
