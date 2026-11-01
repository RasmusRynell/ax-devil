/**
 * One viewer's playback of one entry: the socket, the frames and lane data it holds, the clock and what to ask the
 * backend for. Playing never holds a frame for lane data; after a seek or step it waits briefly. The session is
 * framework-free; time, the display refresh and the socket are injected, and the UI reads `getSnapshot`.
 */

import { CommandRefused, reasonOf } from "../errors";
import {
  PlaybackClock,
  SPEED_STEP,
  clampSpeed,
  followFrame,
  type ClockIntent,
  type FrameSource,
} from "./clock";
import type { LaneControl, LaneSettings, Selection, Surface } from "./contract";
import { dataWindow, sampleBudgetBytes } from "./data/dataWindow";
import { FrameFailures } from "./failures";
import { FrameBuffer, windowSize } from "./FrameBuffer";
import { DataWait, displaySource } from "./frameSource";
import { WantPlanner, sameWant } from "./direction";
import { LaneSet, startWith } from "./lanes";
import {
  PLAYBACK_PROTOCOL,
  type Envelope,
  type ServerMessage,
  type VideoFrame,
  type Want,
} from "./protocol";
import { BUFFER_BUDGET_BYTES, type ResumeState, type SessionDeps } from "./sessionDeps";
import { INITIAL_VIEW, buildView, sameView, type PlaybackView } from "./view";
import { ViewerData } from "./viewerData";
import type { Connection, Incoming } from "./wire";

export type { Surface } from "./contract";

/** Waits between reconnection attempts; the last one repeats. */
const RETRY_MS = [500, 1000, 2000, 4000, 8000];
/**
 * Close codes that end a stream for good, with what the viewer says; other closes are retried.
 */
const ENDED: Record<number, string> = {
  4401: "The backend refused this video stream.",
  4403: "This video stream was ended: access to it was withdrawn.",
};

export class PlaybackSession implements LaneControl {
  readonly #deps: SessionDeps;
  readonly #budget: number;
  readonly #listeners = new Set<() => void>();
  readonly #failures = new FrameFailures();
  #view: PlaybackView = INITIAL_VIEW;
  readonly #lanes = new LaneSet();
  readonly #data = new ViewerData(this.#lanes, (message) => this.#connection?.send(message));
  /** A paused clock waits briefly for lane data after a seek or step. */
  readonly #wait = new DataWait(
    () => this.#deps.now(),
    (callback, ms) => this.#deps.after(callback, ms),
    () => this.#arm(),
  );
  /** What the clock reads: the video held, and the lanes' data or the wait over; playing never waits for data. */
  readonly #source: FrameSource = displaySource(
    {
      has: (id) => this.#buffer?.has(id) === true,
      newestPresentable: (atOrBefore, notBefore) =>
        this.#buffer?.newestPresentable(atOrBefore, notBefore),
    },
    { ready: (id) => this.#data.draw?.ready(id) ?? true },
    this.#wait.since,
    () => this.#deps.now(),
  );
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
   * Changes a lane's settings. What the backend selects by (sync, sticky, visibility) sends a new Lane Policy and
   * blanks the lane until its new Frame Maps arrive; the rest applies to the held samples at once.
   */
  setLane(lane: number, change: Partial<LaneSettings>): void {
    const message = this.#lanes.set(lane, change);
    if (message) this.#connection?.send(message);
    this.#data.refresh();
    this.represent();
    this.#changed();
  }

  /** Gives every other lane of this viewer the settings of `lane`, but its visibility. */
  applyToAll(lane: number): void {
    this.#lanes.copyFrom(lane).forEach((message) => this.#connection?.send(message));
    this.#data.refresh();
    this.represent();
    this.#changed();
  }

  /** The settings of every lane by position, which the next entry's lanes start from. */
  laneSettings(): readonly LaneSettings[] {
    return this.#lanes.states.map((lane) => lane.settings);
  }

  /** Selects an object (`null`: none) for the inspector. Selection is this viewer's own and never reaches the backend. */
  select(selection: Selection): void {
    if (this.#data.inspections.select(selection)) this.#publish();
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
    this.#wait.restart();
    this.#planner?.stepped(delta);
    this.#changed();
    this.#intend();
  }

  /** Jumps to a frame, as scrubbing the seek bar does. */
  seek(sequenceId: number): void {
    if (!this.#clock) return;
    this.#clock.seek(sequenceId);
    this.#wait.restart();
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
    this.#wait.restart();
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
    const current = () => this.#attempt === attempt && !this.#closed;
    this.#deps.ticket().then(
      (ticket) => {
        if (!current()) return;
        this.#connection = this.#deps.connect((incoming) => {
          if (current()) this.#onIncoming(incoming);
        });
        this.#connection.send({
          type: "Open",
          ticket,
          start_frame: this.#resumeAt,
          data_bytes: sampleBudgetBytes(this.#budget),
        });
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
      const before = this.#lanes.states;
      const defaults = startWith(
        message.lane_defaults,
        message.catalogs,
        this.#deps.defaultCatalog?.(),
      );
      this.#lanes
        .open(message.lanes, defaults, this.#deps.lanes)
        .forEach((m) => this.#connection?.send(m));
      this.#data.reopen(message, before);
      this.#data.refresh();
    } else if (message.type === "LaneStatus") {
      if (this.#lanes.setStatus(message.lane, message.status)) {
        this.#data.refresh();
        this.represent();
        this.#arm();
      }
    } else if (message.type === "Catalog" || message.type === "Catalogs") {
      if (this.#data.receiveCatalogs(message)) {
        this.represent();
        this.#onData();
      }
    } else if (message.type === "Inspected") {
      this.#data.inspections.receive(message);
    } else if (message.type === "DataWindow") {
      this.#data.window(message.first, message.last);
    } else if (message.type === "Evict") {
      this.#data.evict(message);
    } else if (message.type === "LaneProblem") {
      this.#lanes.setProblem(message.lane, message.reason);
    } else if (message.type === "FrameError") {
      this.#noteFailure(message.sequence_id, reasonOf(message));
    } else if (message.type === "Error") {
      return this.#fail(reasonOf(message), message.location);
    }
    this.#publish();
  }

  #onEnvelope(envelope: Envelope): void {
    if (envelope.kind === "timeline") {
      if (this.#opened && !this.#clock) this.#ready(this.#opened, envelope.times);
    } else if (envelope.kind === "frame") {
      if (!this.#buffer || !this.#clock) return;
      // A frame the window no longer covers, or that does not fit, is dropped here and freed.
      const { sequenceId } = envelope.frame;
      // A paused clock is waiting for exactly one frame, the requested one.
      if (this.#buffer.add(sequenceId, envelope.frame) && sequenceId === this.#clock.playhead)
        this.#arm();
    } else if (envelope.kind === "sample") {
      const reason = this.#data.addSample(envelope);
      if (reason !== null) return this.#unreadable(reason);
      this.#onData();
    } else if (this.#clock) {
      if (!this.#data.fits(envelope))
        return this.#unreadable("a frame map run reaches past the last frame");
      if (this.#data.addMap(envelope)) this.#onData();
    }
  }

  #unreadable(reason: string): void {
    this.#fail(`The backend sent something this window cannot read: ${reason}`, "playback");
  }

  /** Data arrived: draw the shown frame again if a lane that waited can draw now, and let a held frame go. */
  #onData(): void {
    const clock = this.#clock;
    if (!clock) return;
    const shown = clock.info()?.sequenceId;
    if (shown !== undefined && this.#data.unblocked(shown)) this.represent();
    if (!clock.playing && clock.info()?.sequenceId !== clock.playhead) this.#arm();
  }

  /** Draws a held frame with what the lanes draw on it. */
  #draw(sequenceId: number): void {
    const held = this.#buffer?.get(sequenceId);
    if (held && this.#data.present(held, sequenceId, this.#surface)) this.#publish();
  }

  #ready(opened: Extract<ServerMessage, { type: "Opened" }>, times: Float64Array): void {
    if (times.length === 0 || times.length !== opened.frame_count || opened.frame_bytes <= 0)
      return this.#fail("The backend described the video inconsistently", "playback");
    this.#buffer = new FrameBuffer<VideoFrame>(this.#budget, times.length);
    this.#data.open(times.length, sampleBudgetBytes(this.#budget));
    this.#planner = new WantPlanner(windowSize(this.#budget, opened.frame_bytes));
    this.#times = times;
    this.#clock = new PlaybackClock(times, this.#deps.now, this.#resumeAt ?? 0);
    this.#clock.setSpeed(this.#speed);
    this.#failures.clear();
    this.#status = "ready";
    this.#retries = 0;
    this.#frameCount = opened.frame_count;
    this.#applyFollowed();
    this.#wait.restart();
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
    this.#holdForData(clock, presented !== undefined);
    this.#publish();
    if (clock.playing) this.#arm();
  };

  /** A paused clock holding the video of its frame but not the lanes' data asks for a refresh when the wait is over. */
  #holdForData(clock: PlaybackClock, presented: boolean): void {
    if (clock.playing || presented || clock.info()?.sequenceId === clock.playhead) return;
    if (this.#buffer?.has(clock.playhead)) this.#wait.hold();
  }

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
    const frames = this.#planner.want({ playhead: clock.playhead, playing: clock.playing });
    const want = { ...frames, ...dataWindow(this.#data.sampleBytes(), this.#budget, frames) };
    if (sameWant(this.#lastWant, want)) return;
    this.#lastWant = want;
    buffer.setWindow(want);
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
    this.#data.close();
    this.#wait.stop();
    this.#planner = undefined;
    this.#lastWant = undefined;
    this.#failures.clear();
  }

  #publish(): void {
    const view = buildView({
      status: this.#status,
      progress: this.#progress,
      failure: this.#failure,
      frameCount: this.#frameCount,
      speed: this.#speed,
      clock: this.#clock,
      failures: this.#failures,
      lanes: this.#lanes.states,
      catalogs: this.#data.catalogs.listing,
      opened: this.#opened,
      inspection: this.#data.inspections.view,
    });
    if (sameView(view, this.#view)) return;
    this.#view = view;
    this.#listeners.forEach((listener) => listener());
  }
}
