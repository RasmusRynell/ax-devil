/** What the playback socket delivers to the session, parsed where it was received. */

import { decodeInstructions } from "../overlay/instructions";
import type { OverlayPreparer, PreparedOverlay } from "../overlay/preparer";
import {
  EnvelopeError,
  parseEnvelope,
  type ClientMessage,
  type Envelope,
  type OverlayEnvelope,
  type ServerMessage,
} from "./protocol";

/** The envelopes the session handles as they are; overlays reach it prepared, as `overlays`. */
export type DeliveredEnvelope = Exclude<Envelope, { kind: "overlay" }>;

export type Incoming =
  | { type: "message"; message: ServerMessage }
  | { type: "envelope"; envelope: DeliveredEnvelope }
  /**
   * Overlays prepared for drawing, in the order the text atlas changed: apply every one's uploads in this order,
   * including those the session does not keep.
   */
  | { type: "overlays"; overlays: PreparedOverlay[] }
  /** A message that breaks the protocol; the session ends. */
  | { type: "malformed"; reason: string }
  /** The socket closed, however it ended. */
  | { type: "closed" };

/** One socket to the backend. Messages sent before it is open are held until it is. */
export interface Connection {
  send(message: ClientMessage): void;
  close(): void;
  /** Tells the side that rasterizes overlay text the screen's device pixels per CSS pixel. */
  setDensity?(pixelsPerCssPixel: number): void;
}

/** Opens a socket and reports everything it receives, in order. */
export type Connect = (onIncoming: (incoming: Incoming) => void) => Connection;

/** What a received socket message is before overlays are prepared. */
export type Decoded = Incoming | { type: "overlay"; overlay: OverlayEnvelope };

/** Turns one received socket message into what the receiver handles; `null` for kinds this client ignores. */
export function decode(data: string | ArrayBuffer): Decoded | null {
  try {
    if (typeof data === "string")
      return { type: "message", message: JSON.parse(data) as ServerMessage };
    const envelope = parseEnvelope(data);
    if (!envelope) return null;
    return envelope.kind === "overlay"
      ? { type: "overlay", overlay: envelope }
      : { type: "envelope", envelope };
  } catch (error) {
    if (error instanceof EnvelopeError || error instanceof SyntaxError)
      return { type: "malformed", reason: error.message };
    throw error;
  }
}

/** The ArrayBuffer behind an envelope, which a worker transfers to the main thread instead of copying. */
export function bufferOf(envelope: DeliveredEnvelope): ArrayBufferLike {
  return envelope.kind === "frame" ? envelope.frame.planes[0].buffer : envelope.times.buffer;
}

/** The buffers behind prepared overlays, each once, which a worker transfers instead of copying. */
export function buffersOf(overlays: readonly PreparedOverlay[]): ArrayBuffer[] {
  const buffers = new Set<ArrayBuffer>();
  for (const { prepared } of overlays) {
    for (const array of [prepared.shapes, prepared.texts, prepared.polygons])
      buffers.add(array.buffer as ArrayBuffer);
    for (const upload of prepared.uploads)
      if (upload.type === "region") buffers.add(upload.alpha.buffer as ArrayBuffer);
  }
  return [...buffers];
}

/**
 * The receiving side of one socket, in the worker: turns what arrives into what the session handles. It prepares
 * overlays for drawing, but only those of the window and lanes the session last asked for, which it learns from the
 * messages the session sends.
 */
export class Receiver {
  /** Device pixels per CSS pixel that overlay text is rasterized at. */
  density = 1;
  readonly #preparer: OverlayPreparer;
  readonly #hidden = new Set<number>();
  #window: { first: number; last: number } | undefined;

  constructor(preparer: OverlayPreparer) {
    this.#preparer = preparer;
  }

  /** Handles one received socket message; `null` when there is nothing for the session. */
  receive(data: string | ArrayBuffer): Incoming | null {
    const decoded = decode(data);
    if (decoded?.type !== "overlay") return decoded;
    const { lane, sequenceId, revision, target, opacity, sections } = decoded.overlay;
    const window = this.#window;
    if (!window || sequenceId < window.first || sequenceId > window.last) return null;
    if (this.#hidden.has(lane)) return null;
    try {
      const overlays = this.#preparer.prepare({
        lane,
        sequenceId,
        revision,
        instructions: decodeInstructions(sections),
        target,
        dpr: this.density,
        opacity,
      });
      return overlays.length > 0 ? { type: "overlays", overlays } : null;
    } catch (error) {
      if (error instanceof EnvelopeError) return { type: "malformed", reason: error.message };
      throw error;
    }
  }

  /** Learns the window and hidden lanes from a message the session sends, so held overlays follow them. */
  sent(message: ClientMessage): void {
    if (message.type === "Want") {
      this.#window = {
        first: Math.max(0, message.playhead - message.behind),
        last: message.playhead + message.ahead,
      };
      this.#preparer.retain(this.#window.first, this.#window.last);
    } else if (message.type === "LaneView") {
      if (message.view.visible) this.#hidden.delete(message.lane);
      else {
        this.#hidden.add(message.lane);
        this.#preparer.forgetLane(message.lane);
      }
    }
  }
}
