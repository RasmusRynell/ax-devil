/**
 * One viewer's playback of one entry: the socket, the frames and lane overlays it holds, the clock and what to ask
 * the backend for. A frame is shown once every visible lane that loaded has its overlay for it. The session is
 * framework-free; time, the display refresh and the socket are injected, and the UI reads `getSnapshot`.
 */

import type { AtlasMirror } from "../overlay/mirror";
import type { PreparedOverlay } from "../overlay/preparer";
import type { PreparedDrawing } from "../overlay/prepare";
import { CommandRefused, reasonOf } from "../errors";
import {
  PlaybackClock,
  SPEED_STEP,
  clampSpeed,
  followFrame,
  type ClockIntent,
  type FrameSource,
} from "./clock";
import { FrameFailures } from "./failures";
import { FrameBuffer, windowSize } from "./FrameBuffer";
import { WantPlanner } from "./direction";
import { LaneOverlays } from "./laneOverlays";
import { LaneSet } from "./lanes";
import {
  PLAYBACK_PROTOCOL,
  type LaneViewWire,
  type ServerMessage,
  type TargetWire,
  type VideoFrame,
  type Want,
} from "./protocol";
import { INITIAL_VIEW, sameView, type PlaybackView } from "./view";
import type { Connect, Connection, DeliveredEnvelope, Incoming } from "./wire";

/** What one viewer may hold of decoded frames: it divides into the window the backend is asked to fill. */
export const BUFFER_BUDGET_BYTES = 512 * 1024 * 1024;
/** Waits between reconnection attempts; the last one repeats. */
const RETRY_MS = [500, 1000, 2000, 4000, 8000];
/**
 * The close codes that end a stream for good, with what the viewer says: asking again would be refused, or the
 * backend ended the stream on purpose. Any other close is a lost connection that is worth another try.
 */
const ENDED: Record<number, string> = {
  4401: "The backend refused this video stream.",
  4403: "This video stream was ended: access to it was withdrawn.",
};

export interface SessionDeps {
  /** A ticket for one socket: the first message of every connection spends one. Rejects when it cannot get one. */
  ticket: () => Promise<string>;
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
  /** Told what the clock is doing after every change the viewer makes: the intent a presenter publishes. */
  onIntent?: (intent: PlaybackIntent) => void;
}

/** What the viewer asked the clock to do: the position it is on and whether it runs, at what speed. */
export type PlaybackIntent = { frame: number; playing: boolean; speed: number };

/** What carries a video's viewing from one session to the next, across a reconnection or a viewer shown again. */
export type ResumeState = { frame: number; speed: number };

/**
 * Draws the frame the session presents, at most one per display refresh, with each visible lane's overlay (by lane;
 * `undefined` where a lane has none) and the text atlas page the overlays' texts sample.
 */
export interface Surface {
  present(
    frame: VideoFrame,
    overlays: readonly (PreparedDrawing | undefined)[],
    atlas: AtlasMirror,
  ): void;
}

const sameWant = (a: Want | undefined, b: Want): boolean =>
  a !== undefined &&
  a.playhead === b.playhead &&
  a.ahead === b.ahead &&
  a.behind === b.behind &&
  a.direction === b.direction;

export class PlaybackSession {
  readonly #deps: SessionDeps;
  readonly #budget: number;
  readonly #listeners = new Set<() => void>();
  readonly #failures = new FrameFailures();
  #view: PlaybackView = INITIAL_VIEW;
  readonly #lanes = new LaneSet();
  readonly #overlays = new LaneOverlays(this.#lanes);
  #density: number | undefined;
  #cancelTargets: (() => void) | undefined;
  /** What the clock reads: a frame can be shown when it is held with the overlays of every awaited lane. */
  readonly #source: FrameSource = {
    has: (sequenceId) =>
      this.#buffer?.has(sequenceId) === true && this.#overlays.joined(sequenceId),
    newestPresentable: (atOrBefore, notBefore) =>
      this.#buffer?.newestPresentable(atOrBefore, notBefore, (id) => this.#overlays.joined(id)),
  };
  #surface: Surface | null = null;
  #connection: Connection | undefined;
  /** Names the current connection, so a late message of an earlier one is ignored. */
  #attempt: object = {};
  #opened: Extract<ServerMessage, { type: "Opened" }> | undefined;
  #buffer: FrameBuffer<VideoFrame> | undefined;
  #clock: PlaybackClock | undefined;
  #times: Float64Array | undefined;
  /** The presenter's clock this session was last told to follow, and how to read the backend's time. */
  #followed: { intent: ClockIntent; serverNowUs: () => number } | undefined;
  #planner: WantPlanner | undefined;
  #lastWant: Want | undefined;
  #speed = 1;
  /** The frame to open on after a lost connection. */
  #resumeAt: number | undefined;
  #retries = 0;
  /** Set when a lost connection took a running clock: the reopened one is paused, so the viewer's intent is stale. */
  #stoppedByLoss = false;
  #cancelFrame: (() => void) | undefined;
  #cancelRetry: (() => void) | undefined;
  #closed = false;
  #frameCount = 0;
  #failure: PlaybackView["failure"];
  #status: PlaybackView["status"] = "connecting";
  #progress = 0;

  constructor(deps: SessionDeps) {
    this.#deps = deps;
    this.#budget = deps.budgetBytes ?? BUFFER_BUDGET_BYTES;
    this.#resumeAt = deps.resume?.frame;
    this.#speed = clampSpeed(deps.resume?.speed ?? 1);
  }

  /** Where the next session should open: the position asked for, and the speed. Kept after the video is released. */
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
    this.#cancelTargets?.();
    this.#cancelTargets = undefined;
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
    if (shown) this.#draw(shown.sequenceId);
  }

  /**
   * Changes how a lane is shown (catalog, class filter, sync, sticky, visibility): a new revision the backend answers
   * with overlays for it. Until they arrive the frame keeps the lane's last overlay. A change that leaves the view
   * as it is sends nothing.
   */
  setLaneView(lane: number, change: Partial<LaneViewWire>): void {
    const message = this.#lanes.setView(lane, change);
    if (!message) return;
    this.#connection?.send(message);
    if (change.visible === false) this.#overlays.forget(lane);
    this.represent();
    this.#changed();
  }

  /**
   * Tells the session the size each lane's picture is displayed at, in CSS pixels by lane, and the screen's device
   * pixels per CSS pixel. A lane whose size changed gets a new view, at most once per display refresh however often
   * this is called, so a resize does not ask the backend for every intermediate size.
   */
  setLaneTargets(targets: readonly TargetWire[], density = 1): void {
    this.#lanes.setTargets(targets);
    if (density !== this.#density) {
      this.#density = density;
      this.#connection?.setDensity?.(density);
    }
    this.#cancelTargets ??= this.#deps.frame(() => {
      this.#cancelTargets = undefined;
      this.#lanes.retarget().forEach((message) => this.#connection?.send(message));
      this.#publish();
    });
  }

  play(): void {
    const clock = this.#clock;
    if (!clock || clock.playing) return;
    clock.play();
    this.#planner?.played();
    this.#changed();
    this.#intend();
  }

  pause(): void {
    this.#clock?.pause();
    this.#changed();
    this.#intend();
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
    this.#intend();
  }

  /** Jumps to a frame, as scrubbing the seek bar does. */
  seek(sequenceId: number): void {
    if (!this.#clock) return;
    this.#clock.seek(sequenceId);
    this.#changed();
    this.#intend();
  }

  /** Sets the speed, rounded to hundredths and kept within 0.01 to 10; it stays across a reconnection. */
  setSpeed(speed: number): void {
    this.#speed = clampSpeed(speed);
    this.#clock?.setSpeed(this.#speed);
    this.#changed();
    this.#intend();
  }

  /** One 0.1 step up (`1`) or down (`-1`). */
  stepSpeed(direction: 1 | -1): void {
    this.setSpeed(this.#speed + direction * SPEED_STEP);
  }

  /**
   * Follows a presenter: puts the clock where the presenter's is by now (`serverNowUs` reads the backend's time) and
   * runs it at the presenter's speed. Call it again whenever the presenter's intent changes; between two intents both
   * clocks run alike on their own. A reconnection puts the clock back in step the same way.
   */
  follow(intent: ClockIntent, serverNowUs: () => number): void {
    this.#followed = { intent, serverNowUs };
    this.#applyFollowed();
  }

  #applyFollowed(): void {
    const clock = this.#clock;
    const times = this.#times;
    if (!this.#followed || !clock || !times) return;
    const { intent, serverNowUs } = this.#followed;
    const frame = followFrame(times, intent, serverNowUs());
    this.#speed = clampSpeed(intent.speedCenti / 100);
    clock.setSpeed(this.#speed);
    if (intent.playing && frame < times.length - 1) {
      clock.seek(frame);
      if (!clock.playing) {
        clock.play();
        this.#planner?.played();
      }
    } else {
      clock.pause();
      clock.seek(frame);
    }
    this.#changed();
  }

  /** Tells the viewer what the clock is doing now, after the viewer changed it. */
  #intend(): void {
    // What the viewer does is its own now; the presenter's clock no longer applies, nor after a reconnection.
    this.#followed = undefined;
    const clock = this.#clock;
    if (clock)
      this.#deps.onIntent?.({ frame: clock.playhead, playing: clock.playing, speed: this.#speed });
  }

  #connect(): void {
    const attempt = {};
    this.#attempt = attempt;
    this.#opened = undefined;
    this.#overlays.reconnect();
    const current = () => this.#attempt === attempt && !this.#closed;
    this.#deps.ticket().then(
      (ticket) => {
        if (!current()) return;
        this.#connection = this.#deps.connect((incoming) => {
          if (current()) this.#onIncoming(incoming);
        });
        if (this.#density !== undefined) this.#connection.setDensity?.(this.#density);
        this.#connection.send({ type: "Open", ticket, start_frame: this.#resumeAt });
      },
      (error: unknown) => {
        if (!current()) return;
        // Not getting a ticket because the session's own connection is down is worth another try.
        if (error instanceof CommandRefused) this.#fail(error.message);
        else this.#lose();
      },
    );
  }

  #onIncoming(incoming: Incoming): void {
    if (incoming.type === "message") this.#onMessage(incoming.message);
    else if (incoming.type === "envelope") this.#onEnvelope(incoming.envelope);
    else if (incoming.type === "overlays") this.#onOverlays(incoming.overlays);
    else if (incoming.type === "malformed")
      this.#fail(
        `The backend sent something this window cannot read: ${incoming.reason}`,
        "playback",
      );
    else if (this.#status !== "failed") {
      const ended = ENDED[incoming.code];
      if (ended) this.#fail(ended);
      else this.#lose();
    }
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
      this.#lanes
        .open(message.lanes, message.lane_defaults)
        .forEach((m) => this.#connection?.send(m));
    } else if (message.type === "LaneStatus") {
      if (this.#lanes.setStatus(message.lane, message.status)) this.#arm();
    } else if (message.type === "LaneProblem") {
      this.#lanes.setProblem(message.lane, message.reason);
    } else if (message.type === "FrameError") {
      this.#noteFailure(message.sequence_id, reasonOf(message));
    } else if (message.type === "Error") {
      return this.#fail(reasonOf(message), message.location);
    }
    this.#publish();
  }

  #onEnvelope(envelope: DeliveredEnvelope): void {
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

  /** Overlays the worker prepared: one for the frame on screen is drawn at once, one a paused clock waits for arms it. */
  #onOverlays(overlays: readonly PreparedOverlay[]): void {
    const kept = this.#overlays.accept(overlays);
    if (kept.includes(this.#clock?.info()?.sequenceId ?? -1)) this.represent();
    if (kept.includes(this.#clock?.playhead ?? -1)) this.#arm();
  }

  /** Draws a held frame with the overlays of the visible lanes. */
  #draw(sequenceId: number): void {
    const held = this.#buffer?.get(sequenceId);
    if (!held) return;
    const { overlays, atlas } = this.#overlays.compose(sequenceId);
    this.#surface?.present(held, overlays, atlas);
  }

  #ready(opened: Extract<ServerMessage, { type: "Opened" }>, times: Float64Array): void {
    if (times.length === 0 || times.length !== opened.frame_count || opened.frame_bytes <= 0)
      return this.#fail("The backend described the video inconsistently", "playback");
    this.#buffer = new FrameBuffer<VideoFrame>(this.#budget, times.length);
    this.#overlays.open(times.length);
    this.#planner = new WantPlanner(windowSize(this.#budget, opened.frame_bytes));
    this.#times = times;
    this.#clock = new PlaybackClock(times, this.#deps.now, this.#resumeAt ?? 0);
    this.#clock.setSpeed(this.#speed);
    this.#failures.clear();
    this.#status = "ready";
    this.#retries = 0;
    this.#frameCount = opened.frame_count;
    this.#applyFollowed();
    // The reopened clock is paused: tell the presenter's followers where this viewer really is now.
    if (this.#stoppedByLoss && !this.#followed) this.#intend();
    this.#stoppedByLoss = false;
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
    if (!clock || !this.#buffer) return;
    const wasPlaying = clock.playing;
    const presented = clock.tick(this.#source);
    // Playing ended on its own, at the last frame: the viewer's intent is a pause there, unless it follows a presenter.
    if (wasPlaying && !clock.playing && !this.#followed) this.#intend();
    if (presented !== undefined) this.#draw(presented);
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
    this.#failures.note(sequenceId, reason);
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
    this.#overlays.setWindow(want);
    this.#connection?.send({ type: "Want", ...want });
  }

  /** The socket closed on its own: drop the video and open again where it left off, paused. */
  #lose(): void {
    this.#stoppedByLoss ||= this.#clock?.playing ?? false;
    this.#releaseVideo();
    this.#connection = undefined;
    this.#status = "reconnecting";
    const wait = RETRY_MS[Math.min(this.#retries++, RETRY_MS.length - 1)];
    this.#cancelRetry = this.#deps.after(() => this.#connect(), wait);
    this.#publish();
  }

  #fail(reason: string, location?: string): void {
    this.#failure = { reason, location };
    this.#status = "failed";
    this.#connection?.close();
    this.#connection = undefined;
    this.#releaseVideo();
    this.#publish();
  }

  /** Stops the display loop and drops the clock and every held frame, keeping the position for the next opening. */
  #releaseVideo(): void {
    this.#resumeAt = this.#clock?.playhead ?? this.#resumeAt;
    this.#cancelFrame?.();
    this.#cancelFrame = undefined;
    this.#clock = undefined;
    this.#times = undefined;
    this.#buffer = undefined;
    this.#overlays.close();
    this.#planner = undefined;
    this.#lastWant = undefined;
    this.#failures.clear();
  }

  #publish(): void {
    const clock = this.#clock;
    const shown = clock?.info();
    const playhead = clock?.playhead ?? 0;
    // The failure the screen is waiting on: the latest failed frame at or before the playhead that was not passed.
    const failed = this.#failures.latestAtOrBefore(playhead);
    const frameError = failed && failed.sequenceId > (shown?.sequenceId ?? -1) ? failed : undefined;
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
      lanes: this.#lanes.states,
      catalogs: this.#opened?.catalogs ?? INITIAL_VIEW.catalogs,
      width: this.#opened?.width ?? 0,
      height: this.#opened?.height ?? 0,
      warnings: this.#opened?.warnings ?? INITIAL_VIEW.warnings,
    };
    if (sameView(view, this.#view)) return;
    this.#view = view;
    this.#listeners.forEach((listener) => listener());
  }
}
