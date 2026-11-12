/** The export job of one playback session: what it asked the backend to write and where the backend says it stands. */

import type {
  ClientMessage,
  ExportFormatWire,
  ExportLaneWire,
  ServerMessage,
} from "../generated/api";
import { reasonOf } from "../client/errors";

/** What an export asks of the backend; the job number is the session's to give. */
type ExportRequest = {
  lanes: readonly ExportLaneWire[];
  first: number;
  last: number;
  format: ExportFormatWire;
  path: string;
};

/** How far an export got, in frames. */
type Frames = { done: number; total: number };

/** Where the session's export stands. A finished export stays until `dismiss`, with how far it got. */
export type ExportStatus =
  | { phase: "idle" }
  | ({ phase: "running"; cancelling: boolean } & Frames)
  | ({ phase: "done"; path: string } & Frames)
  | ({ phase: "cancelled" } & Frames)
  | ({ phase: "failed"; reason: string; location?: string } & Frames);

const IDLE: ExportStatus = { phase: "idle" };

/**
 * The one export a viewer's socket can run. The backend runs the job and reports on it; this only asks and tracks.
 * A lost connection ends the job, as the backend cancels an export when the socket closes.
 */
export class ExportJobs {
  readonly #send: (message: ClientMessage) => void;
  readonly #listeners = new Set<() => void>();
  #status: ExportStatus = IDLE;
  #job = 0;
  #path = "";

  /** `send` puts a message on the playback socket. */
  constructor(send: (message: ClientMessage) => void) {
    this.#send = send;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  getSnapshot = (): ExportStatus => this.#status;

  /** Asks the backend to write the export; nothing happens while one is running. */
  start({ lanes, first, last, format, path }: ExportRequest): void {
    if (this.#status.phase === "running") return;
    this.#job += 1;
    this.#path = path;
    this.#set({ phase: "running", done: 0, total: last - first + 1, cancelling: false });
    this.#send({ type: "Export", job: this.#job, lanes: [...lanes], first, last, format, path });
  }

  /** Asks the backend to stop the running export; it says so when it has. */
  cancel(): void {
    const status = this.#status;
    if (status.phase !== "running" || status.cancelling) return;
    this.#send({ type: "CancelExport", job: this.#job });
    this.#set({ ...status, cancelling: true });
  }

  /** The backend's word on a job; one that is not the current job is ignored. */
  receive(message: Extract<ServerMessage, { type: "Export" }>): void {
    if (message.job !== this.#job || this.#status.phase !== "running") return;
    const { state } = message;
    const { cancelling, done, total } = this.#status;
    if (state.state === "Running") {
      this.#set({ phase: "running", done: state.done, total: state.total, cancelling });
    } else if (state.state === "Done")
      this.#set({ phase: "done", path: this.#path, done: total, total });
    else if (state.state === "Cancelled") this.#set({ phase: "cancelled", done, total });
    else
      this.#set({
        phase: "failed",
        reason: reasonOf(state),
        location: state.location,
        done,
        total,
      });
  }

  /** The socket closed: a running export ended with it. */
  lost(): void {
    if (this.#status.phase === "running") {
      const { done, total } = this.#status;
      this.#set({
        phase: "failed",
        reason: "The connection to the backend was lost.",
        done,
        total,
      });
    }
  }

  /** Forgets a finished export. */
  dismiss(): void {
    if (this.#status.phase !== "running") this.#set(IDLE);
  }

  #set(status: ExportStatus): void {
    this.#status = status;
    this.#listeners.forEach((listener) => listener());
  }
}
