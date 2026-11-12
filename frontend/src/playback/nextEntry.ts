import type { ClientMessage } from "../stream/protocol";

/** The entry a viewer would open next, and the labels of the lanes it would show. */
export type PreparedEntry = { entry: string; lanes: readonly string[] };

/**
 * The entry the viewer wishes the backend to prepare in the background, whose settings decide whether it does. The
 * wish is sent while the video is open, and again after every reopening; a new wish replaces the one before.
 */
export class NextEntry {
  readonly #send: (message: ClientMessage) => void;
  #wanted: PreparedEntry | null = null;
  #open = false;

  constructor(send: (message: ClientMessage) => void) {
    this.#send = send;
  }

  /** Wishes for `next`, or for nothing. */
  ask(next: PreparedEntry | null): void {
    this.#wanted = next;
    if (this.#open) this.#tell();
  }

  /** The backend opened the video: it hears the wish, if any. */
  reopen(): void {
    this.#open = true;
    if (this.#wanted) this.#tell();
  }

  /** The video closed: nothing is sent until it opens again. */
  close(): void {
    this.#open = false;
  }

  #tell(): void {
    this.#send({
      type: "Prepare",
      entry: this.#wanted?.entry ?? null,
      lanes: [...(this.#wanted?.lanes ?? [])],
    });
  }
}
