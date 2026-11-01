/**
 * One viewer's playback of one video: the socket, the frames it holds, the clock and what to ask the backend for. The
 * session is framework-free; time, the display refresh and the socket are injected, and the UI reads `getSnapshot`.
 */

import { PlaybackClock, SPEED_STEP, clampSpeed, type FrameInfo } from "./clock";
import { FrameBuffer, windowSize } from "./FrameBuffer";
import { WantPlanner } from "./direction";
import {
  PLAYBACK_PROTOCOL,
  type Envelope,
  type ServerMessage,
  type VideoFrame,
  type Want,
} from "./protocol";
import type { Connect, Connection, Incoming } from "./wire";

/** What one viewer may hold of decoded frames: it divides into the window the backend is asked to fill. */
export const BUFFER_BUDGET_BYTES = 512 * 1024 * 1024;
/** Waits between reconnection attempts; the last one repeats. */
const RETRY_MS = [500, 1000, 2000, 4000, 8000];

export interface SessionDeps {
  connect: Connect;
  /** Monotonic milliseconds. */
  now: () => number;
  /** Calls back once at the next display refresh; returns the way to cancel. */
  frame: (callback: () => void) => () => void;
  /** Calls back once after `ms`; returns the way to cancel. */
  after: (callback: () => void, ms: number) => () => void;
  budgetBytes?: number;
  /** Where an earlier session of this video left off: this one opens there, paused, at that speed. */
  resume?: ResumeState;
}

/** What carries a video's viewing from one session to the next, across a reconnection or a viewer shown again. */
export type ResumeState = { frame: number; speed: number };

/** Draws the frame the session presents; at most one per display refresh. */
export interface Surface {
  present(frame: VideoFrame): void;
}

export type PlaybackView = {
  status: "connecting" | "indexing" | "ready" | "reconnecting" | "failed";
  /** Indexing progress, 0 to 1. */
  progress: number;
  failure?: { reason: string; location: string };
  /** How many frames the video has; 0 until it is ready. */
  frameCount: number;
  playing: boolean;
  speed: number;
  /** The position asked for, which the seek bar shows. */
  playhead: number;
  /** The frame on screen, which the readouts report. */
  shown?: FrameInfo;
  /** Why the frame at the playhead cannot be shown, while it is the one the screen is waiting on. */
  frameError?: { sequenceId: number; reason: string };
};

const sameWant = (a: Want | undefined, b: Want): boolean =>
  a !== undefined &&
  a.playhead === b.playhead &&
  a.ahead === b.ahead &&
  a.behind === b.behind &&
  a.direction === b.direction;

const sameShown = (a: FrameInfo | undefined, b: FrameInfo | undefined): boolean =>
  a === b || (a !== undefined && b !== undefined && a.sequenceId === b.sequenceId);

const sameView = (a: PlaybackView, b: PlaybackView): boolean =>
  a.status === b.status &&
  a.progress === b.progress &&
  a.failure === b.failure &&
  a.frameCount === b.frameCount &&
  a.playing === b.playing &&
  a.speed === b.speed &&
  a.playhead === b.playhead &&
  sameShown(a.shown, b.shown) &&
  a.frameError?.sequenceId === b.frameError?.sequenceId &&
  a.frameError?.reason === b.frameError?.reason;

export class PlaybackSession {
  readonly #contentId: string;
  readonly #deps: SessionDeps;
  readonly #budget: number;
  readonly #listeners = new Set<() => void>();
  readonly #frameErrors = new Map<number, string>();
  /** The ids in `#frameErrors`, ascending, to find the latest one at or before a position without a scan. */
  #failedIds: number[] = [];
  #view: PlaybackView = {
    status: "connecting",
    progress: 0,
    frameCount: 0,
    playing: false,
    speed: 1,
    playhead: 0,
  };
  #surface: Surface | null = null;
  #connection: Connection | undefined;
  /** Names the current connection, so a late message of an earlier one is ignored. */
  #attempt: object = {};
  #opened: Extract<ServerMessage, { type: "Opened" }> | undefined;
  #buffer: FrameBuffer<VideoFrame> | undefined;
  #clock: PlaybackClock | undefined;
  #planner: WantPlanner | undefined;
  #lastWant: Want | undefined;
  #speed = 1;
  /** The frame to open on after a lost connection. */
  #resumeAt: number | undefined;
  #retries = 0;
  #cancelFrame: (() => void) | undefined;
  #cancelRetry: (() => void) | undefined;
  #closed = false;
  #frameCount = 0;
  #failure: PlaybackView["failure"];
  #status: PlaybackView["status"] = "connecting";
  #progress = 0;

  constructor(contentId: string, deps: SessionDeps) {
    this.#contentId = contentId;
    this.#deps = deps;
    this.#budget = deps.budgetBytes ?? BUFFER_BUDGET_BYTES;
    this.#resumeAt = deps.resume?.frame;
    this.#speed = clampSpeed(deps.resume?.speed ?? 1);
  }

  /** Where the next session should open: the position asked for, and the speed. Read before `close`. */
  resumeState(): ResumeState {
    return { frame: this.#clock?.playhead ?? this.#resumeAt ?? 0, speed: this.#speed };
  }

  /** Connects and asks for the video. */
  start(): void {
    this.#connect();
  }

  /** Ends the session: tells the backend, stops the display loop and drops every frame. */
  close(): void {
    this.#closed = true;
    this.#connection?.send({ type: "Close" });
    this.#connection?.close();
    this.#connection = undefined;
    this.#cancelRetry?.();
    this.#releaseVideo();
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  getSnapshot = (): PlaybackView => this.#view;

  /** Sets where frames are drawn; `null` stops drawing. */
  setSurface(surface: Surface | null): void {
    this.#surface = surface;
  }

  /** Draws the shown frame again, for a surface that lost its picture, if the frame is still held. */
  represent(): void {
    const shown = this.#clock?.info();
    const held = shown && this.#buffer?.get(shown.sequenceId);
    if (held) this.#surface?.present(held);
  }

  play(): void {
    const clock = this.#clock;
    if (!clock || clock.playing) return;
    clock.play();
    this.#planner?.played();
    this.#changed();
  }

  pause(): void {
    this.#clock?.pause();
    this.#changed();
  }

  togglePlay(): void {
    if (this.#clock?.playing) this.pause();
    else this.play();
  }

  /** Steps relative to the requested position, pausing first. Holding the key calls this repeatedly. */
  step(delta: number): void {
    if (!this.#clock) return;
    this.#clock.step(delta);
    this.#planner?.stepped(delta);
    this.#changed();
  }

  /** Jumps to a frame, as scrubbing the seek bar does. */
  seek(sequenceId: number): void {
    if (!this.#clock) return;
    this.#clock.seek(sequenceId);
    this.#changed();
  }

  /** Sets the speed, rounded to hundredths and kept within 0.01 to 10; it stays across a reconnection. */
  setSpeed(speed: number): void {
    this.#speed = clampSpeed(speed);
    this.#clock?.setSpeed(this.#speed);
    this.#changed();
  }

  /** One 0.1 step up (`1`) or down (`-1`). */
  stepSpeed(direction: 1 | -1): void {
    this.setSpeed(this.#speed + direction * SPEED_STEP);
  }

  #connect(): void {
    const attempt = {};
    this.#attempt = attempt;
    this.#opened = undefined;
    this.#connection = this.#deps.connect((incoming) => {
      if (this.#attempt === attempt && !this.#closed) this.#onIncoming(incoming);
    });
    this.#connection.send({
      type: "Open",
      content_id: this.#contentId,
      start_frame: this.#resumeAt,
    });
  }

  #onIncoming(incoming: Incoming): void {
    if (incoming.type === "message") this.#onMessage(incoming.message);
    else if (incoming.type === "envelope") this.#onEnvelope(incoming.envelope);
    else if (incoming.type === "malformed")
      this.#fail(
        `The backend sent something this window cannot read: ${incoming.reason}`,
        "playback",
      );
    else if (this.#status !== "failed") this.#lose();
  }

  #onMessage(message: ServerMessage): void {
    if (message.type === "Indexing") {
      this.#status = "indexing";
      this.#progress = message.progress;
    } else if (message.type === "Opened") {
      if (message.protocol !== PLAYBACK_PROTOCOL)
        return this.#fail(
          `The backend speaks playback protocol ${message.protocol}, this window ${PLAYBACK_PROTOCOL}`,
          "playback",
        );
      this.#opened = message;
    } else if (message.type === "FrameError") {
      this.#noteFailure(message.sequence_id, message.reason);
    } else if (message.type === "Error") {
      return this.#fail(message.reason, message.location);
    }
    this.#publish();
  }

  #onEnvelope(envelope: Envelope): void {
    if (envelope.kind === "timeline") {
      if (this.#opened && !this.#clock) this.#ready(this.#opened, envelope.times);
    } else if (this.#buffer && this.#clock) {
      // A frame the window no longer covers, or that does not fit, is dropped here and freed.
      const { sequenceId } = envelope.frame;
      // A paused clock is waiting for exactly one frame, the requested one.
      if (this.#buffer.add(sequenceId, envelope.frame) && sequenceId === this.#clock.playhead)
        this.#arm();
    }
  }

  #ready(opened: Extract<ServerMessage, { type: "Opened" }>, times: Float64Array): void {
    if (times.length === 0 || times.length !== opened.frame_count || opened.frame_bytes <= 0)
      return this.#fail("The backend described the video inconsistently", "playback");
    this.#buffer = new FrameBuffer<VideoFrame>(this.#budget, times.length);
    this.#planner = new WantPlanner(windowSize(this.#budget, opened.frame_bytes));
    this.#clock = new PlaybackClock(times, this.#deps.now, this.#resumeAt ?? 0);
    this.#clock.setSpeed(this.#speed);
    this.#frameErrors.clear();
    this.#status = "ready";
    this.#retries = 0;
    this.#frameCount = opened.frame_count;
    this.#sendWant();
    this.#publish();
  }

  /**
   * One display refresh: present the newest due frame, if any, then report the window. Refreshes are only asked for
   * while playing and when something changed or a frame the paused clock waits for arrived; an idle session asks for
   * none.
   */
  #tick = (): void => {
    this.#cancelFrame = undefined;
    const clock = this.#clock;
    const buffer = this.#buffer;
    if (!clock || !buffer) return;
    const presented = clock.tick(buffer);
    const held = presented === undefined ? undefined : buffer.get(presented);
    if (held) this.#surface?.present(held);
    this.#sendWant();
    this.#publish();
    if (clock.playing) this.#arm();
  };

  #arm(): void {
    if (this.#clock && !this.#cancelFrame) this.#cancelFrame = this.#deps.frame(this.#tick);
  }

  /** The user moved something: show it, and report the window on the next refresh. */
  #changed(): void {
    this.#arm();
    this.#publish();
  }

  #noteFailure(sequenceId: number, reason: string): void {
    if (!this.#frameErrors.has(sequenceId)) {
      this.#failedIds.push(sequenceId);
      this.#failedIds.sort((a, b) => a - b);
    }
    this.#frameErrors.set(sequenceId, reason);
    this.#clock?.skip(sequenceId);
  }

  /** Reports the window and sends it when it differs from the last one; the buffer keeps exactly that window. */
  #sendWant(): void {
    const clock = this.#clock;
    const buffer = this.#buffer;
    if (!clock || !buffer || !this.#planner) return;
    const want = this.#planner.want({ playhead: clock.playhead, playing: clock.playing });
    if (sameWant(this.#lastWant, want)) return;
    this.#lastWant = want;
    buffer.setWindow(want);
    this.#connection?.send({ type: "Want", ...want });
  }

  /** The socket closed on its own: drop the video and open again where it left off, paused. */
  #lose(): void {
    this.#resumeAt = this.#clock?.playhead ?? this.#resumeAt;
    this.#releaseVideo();
    this.#connection = undefined;
    this.#status = "reconnecting";
    const wait = RETRY_MS[Math.min(this.#retries++, RETRY_MS.length - 1)];
    this.#cancelRetry = this.#deps.after(() => this.#connect(), wait);
    this.#publish();
  }

  #fail(reason: string, location: string): void {
    this.#failure = { reason, location };
    this.#status = "failed";
    this.#connection?.close();
    this.#connection = undefined;
    this.#releaseVideo();
    this.#publish();
  }

  /** Stops the display loop and drops the clock and every held frame. */
  #releaseVideo(): void {
    this.#cancelFrame?.();
    this.#cancelFrame = undefined;
    this.#clock = undefined;
    this.#buffer = undefined;
    this.#planner = undefined;
    this.#lastWant = undefined;
    this.#frameErrors.clear();
    this.#failedIds = [];
  }

  #publish(): void {
    const clock = this.#clock;
    const shown = clock?.info();
    const playhead = clock?.playhead ?? 0;
    // The failure the screen is waiting on: the latest failed frame at or before the playhead that was not passed.
    let frameError: PlaybackView["frameError"];
    const failed = this.#latestFailureAtOrBefore(playhead);
    if (failed !== undefined && failed > (shown?.sequenceId ?? -1))
      frameError = { sequenceId: failed, reason: this.#frameErrors.get(failed) ?? "" };
    const view: PlaybackView = {
      status: this.#status,
      progress: this.#progress,
      failure: this.#failure,
      frameCount: this.#status === "ready" ? this.#frameCount : 0,
      playing: clock?.playing ?? false,
      speed: this.#speed,
      playhead,
      shown,
      frameError,
    };
    if (sameView(view, this.#view)) return;
    this.#view = view;
    this.#listeners.forEach((listener) => listener());
  }

  /** The highest failed sequence id that is at or before `position`, by binary search. */
  #latestFailureAtOrBefore(position: number): number | undefined {
    const ids = this.#failedIds;
    let low = 0;
    let high = ids.length;
    while (low < high) {
      const middle = (low + high) >> 1;
      if (ids[middle] <= position) low = middle + 1;
      else high = middle;
    }
    return ids[low - 1];
  }
}
