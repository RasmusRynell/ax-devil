/**
 * One viewer's live stream: the playback socket opened for a live video, the newest frame and what its lane draws on
 * it, where the video and the overlay stand, and the Events list. There is no clock and no history: the newest frame
 * that has its overlay data is drawn at the next display refresh, and pausing only stops showing newer ones. The
 * session is framework-free; the socket, the display refresh and time are injected, and the UI reads `getSnapshot`.
 */

import type { ErrorWire, LiveOpenedWire } from "../generated/api";
import type { FileScope } from "../inspect/fileScope";
import type { FrameFields } from "../inspect/frameFields";
import type { LaneDisplay, LaneFrame, LaneSettings, Selection, Surface } from "../lanes/contract";
import { sampleBudgetBytes } from "../stream/data/dataWindow";
import { LaneSet } from "../lanes/lanes";
import type { LaneMatch } from "../lanes/laneData";
import {
  PLAYBACK_PROTOCOL,
  type Envelope,
  type ServerMessage,
  type VideoFrame,
} from "../stream/protocol";
import { BUFFER_BUDGET_BYTES, ENDED, RETRY_MS, type StreamDeps } from "../playback/streamDeps";
import { ViewerData } from "../playback/viewerData";
import type { Connection, Incoming } from "../stream/wire";
import { EventLog } from "./eventLog";
import { REST, sameLive, viewOf, type LiveView, type Parts } from "./view";

export type { LiveView } from "./view";

/** What a live session is given: the playback stream's way to a socket, the display and time, and its lane hooks. */
export type LiveDeps = Pick<
  StreamDeps,
  | "ticket"
  | "connect"
  | "frame"
  | "after"
  | "defaultCatalog"
  | "savedDefaults"
  | "lanes"
  | "onPolicy"
>;

/** A frame number past any a stream reaches, for the maps' upper bound: a live stream has no last frame. */
const NEVER = Number.MAX_SAFE_INTEGER;
/** How many frames wait for their lane's data at most; the oldest is let go. */
const QUEUED = 8;
/** How long a frame waits for its lane's data before it is drawn without; the backend sends the two together. */
const LATE_MS = 250;
/** How many Sample Items the viewer holds at most: the overlay's decoder is stateful, so the newest few are enough. */
const MAX_HELD = 256;

export class LiveSession implements LaneDisplay {
  readonly #deps: LiveDeps;
  readonly #listeners = new Set<() => void>();
  readonly #lanes = new LaneSet((slot, policy) => this.#deps.onPolicy?.(slot, policy));
  readonly #data = new ViewerData(
    this.#lanes,
    (message) => this.#connection?.send(message),
    (callback, ms) => this.#deps.after(callback, ms),
    () => this.#publish(),
  );
  /** The Events list, which records from the moment the stream opens whether or not anything shows it. */
  readonly events = new EventLog();
  #view: LiveView = viewOf(REST);
  #surface: Surface | null = null;
  #connection: Connection | undefined;
  /** Names this connection, so a late message of an earlier one is ignored. */
  #attempt: object = {};
  /** Whether the backend has opened the stream on this connection. */
  #opened = false;
  /** The frames received that are not drawn yet, oldest first. */
  #queued: VideoFrame[] = [];
  /** The frame on screen. */
  #presented: VideoFrame | undefined;
  /** The indices of the Sample Items held, oldest first. */
  #held: number[] = [];
  #cancelFrame: (() => void) | undefined;
  #cancelLate: (() => void) | undefined;
  /** The queued frames have waited long enough for their data. */
  #overdue = false;
  #cancelRetry: (() => void) | undefined;
  #retries = 0;
  #closed = false;
  #parts: Parts = { ...REST };

  constructor(deps: LiveDeps) {
    this.#deps = deps;
  }

  /** Connects and opens the stream: the camera is not contacted before this. */
  start(): void {
    this.#connect();
  }

  /** Stops the stream for good: tells the backend, which ends its connections, and drops every frame. */
  close(): void {
    this.#closed = true;
    this.#connection?.send({ type: "Close" });
    this.#connection?.close();
    this.#connection = undefined;
    this.#cancelRetry?.();
    this.#release();
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  getSnapshot = (): LiveView => this.#view;

  /** Stops showing newer frames. The connection stays up and the Events list keeps recording. */
  pause(): void {
    if (this.#parts.paused || this.#parts.status !== "ready") return;
    this.#parts.paused = true;
    this.#queued = [];
    this.#overdue = false;
    this.#connection?.send({ type: "Live", action: "pause" });
    this.#publish();
  }

  /** Shows the newest frame again, from the next one. */
  resume(): void {
    if (!this.#parts.paused) return;
    this.#parts.paused = false;
    this.#connection?.send({ type: "Live", action: "resume" });
    this.#publish();
  }

  /** Asks the backend to reopen every transport of a failed or stalled feed; the backend resumes delivery, so this does too. */
  retry(): void {
    this.#connection?.send({ type: "Live", action: "retry" });
    if (!this.#parts.paused) return;
    this.#parts.paused = false;
    this.#publish();
  }

  /** The window began following a presenter: the backend resumes delivery by itself, so the pause ends here without a message. */
  follow(): void {
    if (!this.#parts.paused) return;
    this.#parts.paused = false;
    this.#publish();
  }

  /** The lists of the lanes' whole files; a live stream has none, but the objects panel reads it. */
  get files(): FileScope {
    return this.#data.files;
  }

  /** The leaves of the shown frames' objects at the paths a search names. */
  get fields(): FrameFields {
    return this.#data.fields;
  }

  /** What `lane` drew on the frame on screen. */
  shown(lane: number): LaneFrame | undefined {
    return this.#data.shown(lane);
  }

  matched(lane: number): LaneMatch | null {
    return this.#data.matched(lane);
  }

  /** A live stream has no timeline to read a frame's time from. */
  frameTime(): undefined {
    return undefined;
  }

  /** There is no other frame to go to; the objects panel asks only when it was told a seat may seek. */
  seek(): void {}

  setSurface(surface: Surface | null): void {
    this.#surface = surface;
    if (surface) this.represent();
  }

  /** Redraws the retained presentation; timing edits apply to future live pictures. */
  represent(): void {
    const frame = this.#presented;
    if (!frame) return;
    const changed = this.#data.draw?.ready(frame.sequenceId)
      ? this.#data.present(frame, frame.sequenceId, this.#surface)
      : this.#data.redraw(frame, this.#surface);
    if (changed) this.#publish();
  }

  /** Changes a lane's settings; what the backend selects by (sticky, visibility) is sent. */
  setLane(lane: number, change: Partial<LaneSettings>): void {
    const message = this.#lanes.set(lane, change);
    if (message) this.#connection?.send(message);
    this.#data.refresh();
    this.represent();
    this.#publish();
  }

  /** Gives every other lane of this viewer the settings of `lane`, but its visibility. */
  async applyToAll(lane: number, reread = true): Promise<void> {
    (await this.#data.applyToAll(lane, reread)).forEach((message) =>
      this.#connection?.send(message),
    );
    this.#data.refresh();
    this.represent();
    this.#publish();
  }

  /** Dismisses the lane's Apply refusal. */
  dismissCatalogAction(lane: number): void {
    this.#data.dismissCatalogAction(lane);
    this.#publish();
  }

  /** Asks the backend for the catalog `lane` chose again; the answer is compiled when it arrives. */
  reloadCatalog(lane: number): void {
    this.#data.reloadCatalog(lane);
  }

  /** The settings of every lane by position, which the next stream's lanes start from. */
  laneSettings(): readonly LaneSettings[] {
    return this.#lanes.states.map((lane) => lane.settings);
  }

  /** Whether an object page shows; its fields are asked for only then. */
  showInspector(shown: boolean, pins?: readonly import("../inspect/fields").FieldId[]): void {
    const changed = this.#data.inspections.setVisible(shown);
    if ((pins ? this.#data.inspections.setPins(pins) : false) || changed) this.#publish();
  }

  /** Selects an object (`null`: none) for the inspector. Selection is this viewer's own. */
  select(selection: Selection): void {
    if (this.#data.inspections.select(selection)) this.#publish();
  }

  #connect(): void {
    const attempt = {};
    this.#attempt = attempt;
    this.#opened = false;
    const current = () => this.#attempt === attempt && !this.#closed;
    this.#deps.ticket().then(
      (ticket) => {
        if (!current()) return;
        this.#connection = this.#deps.connect((incoming) => {
          if (current()) this.#onIncoming(incoming);
        });
        this.#connection.send({ type: "Open", ticket, data_bytes: this.#budget() });
      },
      () => {
        // Not getting a ticket because the session's own connection is down is worth another try.
        if (current()) this.#lose();
      },
    );
  }

  #budget(): number {
    return sampleBudgetBytes(BUFFER_BUDGET_BYTES);
  }

  #onIncoming(incoming: Incoming): void {
    if (incoming.type === "message") this.#onMessage(incoming.message);
    else if (incoming.type === "envelope") this.#onEnvelope(incoming.envelope);
    else if (incoming.type === "malformed") this.#unreadable(incoming.reason);
    else if (this.#parts.status !== "failed") {
      const ended = ENDED[incoming.code];
      if (ended) this.#fail(ended);
      else this.#lose();
    }
  }

  #onMessage(message: ServerMessage): void {
    if (message.type === "LiveOpened") {
      if (message.protocol !== PLAYBACK_PROTOCOL)
        return this.#fail(
          `The backend speaks playback protocol ${message.protocol}, this window ${PLAYBACK_PROTOCOL}`,
          "playback",
        );
      this.#open(message);
    } else if (message.type === "LiveStatus") {
      this.#parts.alternatives = message.alternatives;
      const { video, overlay, behind, waiting } = message;
      Object.assign(this.#parts, { video, overlay, behind, waiting });
    } else if (message.type === "LiveEvents") {
      return this.events.addEvents(message);
    } else if (message.type === "LiveGap") {
      return this.events.addGap(message.time_us);
    } else if (message.type === "LaneStatus") {
      if (this.#lanes.setStatus(message.lane, message.status)) {
        this.#data.refresh();
        this.represent();
        this.#arm();
      }
    } else if (message.type === "Catalog" || message.type === "Catalogs") {
      if (this.#data.receiveCatalogs(message)) {
        this.represent();
        this.#arm();
      }
    } else if (message.type === "Error") {
      return this.#fail(message);
    } else {
      this.#data.receive(message);
    }
    this.#publish();
  }

  /** The stream opened: the lane opens (a plain one when there is no overlay) and the maps start empty. */
  #open({ lane, lane_defaults, catalogs }: LiveOpenedWire): void {
    this.#opened = true;
    this.#data.reopen(
      { lanes: [lane], lane_defaults, catalogs },
      this.#deps.defaultCatalog?.(),
      this.#deps.lanes,
      this.#deps.savedDefaults?.(),
    );
    this.#data.open(NEVER, this.#budget());
    this.#data.window(0, NEVER);
    Object.assign(this.#parts, { status: "ready", paused: false, failure: undefined });
    this.#retries = 0;
  }

  #onEnvelope(envelope: Envelope): void {
    if (!this.#opened) return;
    if (envelope.kind === "frame") {
      // What arrives after a pause was already on its way: the picture stays as it was paused.
      if (this.#parts.paused) return;
      this.#queued = [...this.#queued, envelope.frame].slice(-QUEUED);
      this.#arm();
    } else if (envelope.kind === "sample") {
      const reason = this.#data.addSample(envelope);
      if (reason !== null) return this.#unreadable(reason);
      this.#held.push(envelope.sample);
      this.#arm();
    } else if (envelope.kind === "map") {
      if (this.#data.addMap(envelope)) this.#arm();
    }
  }

  #unreadable(reason: string): void {
    this.#fail(`The backend sent something this window cannot read: ${reason}`, "playback");
  }

  #arm(): void {
    if (this.#queued.length > 0 && !this.#cancelFrame)
      this.#cancelFrame = this.#deps.frame(this.#tick);
  }

  /**
   * One display refresh: draws the newest frame whose lane data has arrived, and lets go of the older ones. Frames
   * that wait cost nothing: the data arriving, a newer frame or the wait running out arms the next refresh.
   */
  #tick = (): void => {
    this.#cancelFrame = undefined;
    const draw = this.#data.draw;
    if (!draw || this.#parts.paused) return;
    let at = this.#queued.length - 1;
    if (!this.#overdue) while (at >= 0 && !draw.ready(this.#queued[at].sequenceId)) at--;
    if (at < 0) return this.#wait();
    const frame = this.#queued[at];
    this.#queued = this.#queued.slice(at + 1);
    this.#overdue = false;
    this.#cancelLate?.();
    this.#cancelLate = undefined;
    this.#presented = frame;
    this.#data.window(frame.sequenceId, NEVER);
    this.#data.present(frame, frame.sequenceId, this.#surface);
    this.#prune();
    this.#publish();
  };

  /** Gives the data of the queued frames a moment to arrive, then draws them as they are. */
  #wait(): void {
    this.#cancelLate ??= this.#deps.after(() => {
      this.#cancelLate = undefined;
      this.#overdue = true;
      this.#arm();
    }, LATE_MS);
  }

  /** Lets go of the Sample Items the frames to come cannot use: older than the one on screen, or past the newest few. */
  #prune(): void {
    const ready = this.#lanes.awaited(0);
    const source = this.#lanes.states[0]?.source;
    if (!ready || source == null) return;
    const used = this.#data.matched(0)?.sample ?? null;
    let drop = Math.max(0, this.#held.length - MAX_HELD);
    while (used !== null && drop < this.#held.length && this.#held[drop] < used) drop++;
    if (drop === 0) return;
    const samples = this.#held.splice(0, drop);
    this.#data.evict({ type: "Evict", source, data_revision: ready.data_revision, samples });
  }

  /** The socket closed on its own: drop the picture and open again, which starts the stream anew. */
  #lose(): void {
    this.#connection = undefined;
    this.#release();
    this.#parts.status = "reconnecting";
    const wait = RETRY_MS[Math.min(this.#retries++, RETRY_MS.length - 1)];
    this.#cancelRetry = this.#deps.after(() => this.#connect(), wait);
    this.#publish();
  }

  #fail(error: string | ErrorWire, location?: string): void {
    this.#parts.failure =
      typeof error === "string" ? { code: "Internal", params: {}, reason: error, location } : error;
    this.#parts.status = "failed";
    this.#connection?.close();
    this.#connection = undefined;
    this.#release();
    this.#publish();
  }

  /** Stops the display loop and drops the picture and everything held; the Events list stays. */
  #release(): void {
    this.#cancelFrame?.();
    this.#cancelFrame = undefined;
    this.#cancelLate?.();
    this.#cancelLate = undefined;
    this.#overdue = false;
    this.#queued = [];
    this.#presented = undefined;
    this.#held = [];
    this.#data.close();
    const { video, overlay, behind, waiting } = REST;
    Object.assign(this.#parts, { video, overlay, behind, waiting });
  }

  #publish(): void {
    const view = viewOf(this.#parts, {
      lanes: this.#lanes.states,
      catalogs: this.#data.catalogs.listing,
      inspection: this.#data.inspections.view,
      frame: this.#presented,
    });
    if (sameLive(view, this.#view)) return;
    this.#view = view;
    this.#listeners.forEach((listener) => listener());
  }
}
