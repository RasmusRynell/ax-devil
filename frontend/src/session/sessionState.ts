import type {
  CursorOptions,
  DocWire,
  SeatInfo,
  SessionServerMessage,
  View,
} from "../generated/api";
import type { Ending, Status } from "./connection";

export type SeatPart = Extract<SessionServerMessage, { type: "seat" }>;

/** Everything a component may read of the window's session; each part keeps its identity until it changes. */
export interface SessionState {
  connection: Exclude<Status["state"], "ended">;
  /** Why the session is over for this window, until it is dismissed. */
  ended: Ending | null;
  session: string | null;
  /** This window's seat id. */
  you: number | null;
  /** Whether the window is in a Session it did not start: one it attached to or joined. */
  attached: boolean;
  /** `null` until the backend has sent the first Doc. */
  doc: DocWire | null;
  /** What this seat may do and how it shows; `null` until the first part arrives. */
  seat: SeatPart | null;
  roster: readonly SeatInfo[];
  cursorOptions: CursorOptions;
  /** What the window shows now, including a change the backend has not settled yet. */
  view: View;
  /** Whether the window shows the presenter's view and nothing of its own. */
  following: boolean;
  /**
   * Whether the seat is the one the backend derived from this Doc. A Doc is published as it arrives, before the seat
   * that goes with it, so until then `seat` (and `following`) still describe the Doc before.
   */
  settled: boolean;
  /** Whether the backend's clock is known well enough to follow the presenter's. */
  synced: boolean;
  /**
   * How many times the backend has seated this window, first connection and every resume alike. A playback stream is
   * granted to one of them: a stream opened under an earlier one is ended.
   */
  seatings: number;
  /** How many workspace commands the backend has answered, whatever the answer: the files they touch have settled. */
  workspaceAnswers: number;
}
