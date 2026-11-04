/**
 * One viewer's playback of one entry: the socket, the frames and lane data it holds, the clock and what to ask the
 * backend for. Playing never holds a frame for lane data; after a seek or step it waits briefly. The session is
 * framework-free; time, the display refresh and the socket are injected, and the UI reads `getSnapshot`.
 */

import { cadence, type Cadence } from "./cadence";
import { reasonOf } from "../errors";
import { ExportJobs } from "../export/jobs";
import {
  PlaybackClock,
  clampSpeed,
  steppedSpeed,
  type ClockIntent,
  type FrameSource,
} from "./clock";
import type { LaneControl, LaneFrame, LaneSettings, Selection, Surface } from "./contract";
import { dataWindow, sampleBudgetBytes } from "./data/dataWindow";
import { FrameFailures } from "./failures";
import type { FileScope } from "./fileScope";
import { followClock } from "./follow";
import { FrameBuffer, windowSize } from "./FrameBuffer";
import { DataWait, displaySource } from "./frameSource";
import { WantPlanner, sameWant } from "./direction";
import type { LaneMatch } from "./laneData";
import { LaneSet } from "./lanes";
import { Link } from "./link";
import {
  PLAYBACK_PROTOCOL,
  type Envelope,
  type ServerMessage,
  type VideoFrame,
  type Want,
} from "./protocol";
import { BUFFER_BUDGET_BYTES, ENDED, type ResumeState, type SessionDeps } from "./sessionDeps";
import { INITIAL_VIEW, buildView, sameView, type PlaybackView } from "./view";
import { ViewerData } from "./viewerData";
import type { Incoming } from "./wire";

export type { Surface } from "./contract";

export class PlaybackSession implements LaneControl {
  readonly #deps: SessionDeps;
  readonly #budget: number;
  readonly #listeners = new Set<() => void>();
  readonly #failures = new FrameFailures();
  #view: PlaybackView = INITIAL_VIEW;
  readonly #lanes = new LaneSet((slot, policy) => this.#deps.onPolicy?.(slot, policy));
  readonly #data = new ViewerData(
    this.#lanes,
    (message) => this.#link.send(message),
    (callback, ms) => this.#deps.after(callback, ms),
  );
  /** A paused clock waits briefly for lane data after a seek or step. */
  readonly #wait = new DataWait(
    () => this.#deps.now(),
    (callback, ms) => this.#deps.after(callback, ms),
    () => this.#arm(),
  );
  /** What the clock reads: the video held, and the lanes' data or the wait over; playing never waits. */
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
  /** The export this viewer's socket runs for the owner. */
  readonly exports = new ExportJobs((message) => this.#link.send(message));
  readonly #link: Link;
  #surface: Surface | null = null;
  #opened: Extract<ServerMessage, { type: "Opened" }> | undefined;
  #buffer: FrameBuffer<VideoFrame> | undefined;
  #clock: PlaybackClock | undefined;
  #times: Float64Array | undefined;
  #cadence: Cadence | null = null;
  /** The presenter's clock last followed, and how to read the backend's time. */
  #followed: { intent: ClockIntent; serverNowUs: () => number } | undefined;
  #planner: WantPlanner | undefined;
  #lastWant: Want | undefined;
  #speed = 1;
  /** The frame to reopen on. */
  #resumeAt: number | undefined;
  /** An ordinary open plays from its first frame once that is shown, unless the viewer acts first. */
  #autoplay: boolean;
  /** A lost connection took a running clock: the reopened one is paused, so the intent is stale. */
  #stoppedByLoss = false;
  #cancelFrame: (() => void) | undefined;
  #frameCount = 0;
  #failure: PlaybackView["failure"];
  #status: PlaybackView["status"] = "connecting";
  #progress = 0;
  /** The frames the backend holds decoded. */
  #cached: PlaybackView["cached"] = [];

  constructor(deps: SessionDeps) {
    this.#deps = deps;
    this.#budget = deps.budgetBytes ?? BUFFER_BUDGET_BYTES;
    this.#resumeAt = deps.resume?.frame;
    this.#autoplay = deps.autoplay === true;
    this.#speed = clampSpeed(deps.resume?.speed ?? 1);
    this.#link = new Link(deps, {
      open: () => ({
        type: "Open",
        start_frame: this.#resumeAt,
        data_bytes: sampleBudgetBytes(this.#budget),
      }),
      incoming: (incoming) => this.#onIncoming(incoming),
      refused: (reason) => this.#fail(reason),
      lost: () => this.#lose(),
    });
  }

  /** Where the next session should open: the position and the speed. Kept after the video is released. */
  resumeState(): ResumeState {
    return { frame: this.#clock?.playhead ?? this.#resumeAt ?? 0, speed: this.#speed };
  }

  /** Connects and asks for the video. */
  start(): void {
    this.#link.connect();
  }

  /** Ends the session: tells the backend, stops the display loop and drops every frame. */
  close(): void {
    this.#link.close();
    this.#releaseVideo();
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  getSnapshot = (): PlaybackView => this.#view;

  /** The lists of the lanes' whole files. */
  get files(): FileScope {
    return this.#data.files;
  }

  /** What `lane` drew on the frame on screen, which Frame scope lists. */
  shown(lane: number): LaneFrame | undefined {
    return this.#data.shown(lane);
  }

  /** How `lane` matched a sample to the frame on screen. */
  matched(lane: number): LaneMatch | null {
    return this.#data.matched(lane);
  }

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
    if (message) this.#link.send(message);
    this.#data.refresh();
    this.represent();
    this.#changed();
  }

  /** Gives every other lane of this viewer the settings of `lane`, but its visibility. */
  applyToAll(lane: number): void {
    this.#lanes.copyFrom(lane).forEach((message) => this.#link.send(message));
    this.#data.refresh();
    this.represent();
    this.#changed();
  }

  /** The settings of every lane by position, which the next entry's lanes start from. */
  laneSettings(): readonly LaneSettings[] {
    return this.#lanes.states.map((lane) => lane.settings);
  }

  /** Selects an object (`null`: none) for the inspector; the selection never reaches the backend. */
  select(selection: Selection): void {
    if (this.#data.inspections.select(selection)) this.#publish();
  }

  /** Whether an inspector page shows; its fields are asked for only then. */
  showInspector(shown: boolean): void {
    if (this.#data.inspections.setVisible(shown)) this.#publish();
  }

  play(): void {
    this.#autoplay = false;
    const clock = this.#clock;
    if (!clock || clock.playing) return;
    clock.play();
    this.#planner?.played();
    this.#changed();
    this.#intend();
  }

  pause(): void {
    this.#autoplay = false;
    this.#clock?.pause();
    this.#changed();
    this.#intend();
  }

  togglePlay(): void {
    if (this.#clock?.playing) this.pause();
    else this.play();
  }

  /** Steps relative to the requested position. Holding the key calls this repeatedly. */
  step(delta: number): void {
    if (!this.#clock) return;
    this.#autoplay = false;
    this.#clock.step(delta);
    this.#wait.restart();
    this.#planner?.stepped(delta);
    this.#changed();
    this.#intend();
  }

  /** A frame's time in microseconds. */
  frameTime(sequenceId: number): number | undefined {
    return this.#times?.[sequenceId];
  }

  /** Jumps to a frame; playing goes on from there. */
  seek(sequenceId: number): void {
    if (!this.#clock) return;
    this.#autoplay = false;
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

  /** One 0.1 step up (`1`) or down (`-1`), never below 0.1. */
  stepSpeed(direction: 1 | -1): void {
    this.setSpeed(steppedSpeed(this.#speed, direction));
  }

  /**
   * Follows a presenter: puts the clock where the presenter's is by now (`serverNowUs` reads the backend's time) and
   * runs it at the presenter's speed. Call it again whenever the presenter's intent changes; between two intents both
   * clocks run alike. A reconnection puts the clock back in step the same way.
   */
  follow(intent: ClockIntent, serverNowUs: () => number): void {
    this.#autoplay = false;
    this.#followed = { intent, serverNowUs };
    this.#data.files.setOwner(false);
    this.#applyFollowed();
  }

  #applyFollowed(): void {
    const clock = this.#clock;
    const times = this.#times;
    if (!this.#followed || !clock || !times) return;
    const { intent, serverNowUs } = this.#followed;
    const { speed, started } = followClock(clock, times, intent, serverNowUs());
    this.#speed = speed;
    if (started) this.#planner?.played();
    this.#wait.restart();
    this.#changed();
  }

  /** Tells the viewer what the clock is doing now, after the viewer changed it. */
  #intend(): void {
    // The viewer's own action ends following the presenter, even after a reconnection.
    this.#followed = undefined;
    this.#data.files.setOwner(true);
    const clock = this.#clock;
    if (clock)
      this.#deps.onIntent?.({ frame: clock.playhead, playing: clock.playing, speed: this.#speed });
  }

  #onIncoming(incoming: Incoming): void {
    if (incoming.type === "message") this.#onMessage(incoming.message);
    else if (incoming.type === "envelope") this.#onEnvelope(incoming.envelope);
    else if (incoming.type === "malformed") this.#unreadable(incoming.reason);
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
      this.#data.reopen(
        message,
        this.#deps.defaultCatalog?.(),
        this.#deps.lanes,
        this.#deps.defaultTiming?.(),
      );
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
    } else if (message.type === "Cached") {
      this.#cached = message.ranges;
    } else if (message.type === "Export") {
      this.exports.receive(message);
    } else if (message.type === "FrameError") {
      this.#noteFailure(message.sequence_id, reasonOf(message));
    } else if (message.type === "Error") {
      return this.#fail(reasonOf(message), message.location);
    } else {
      this.#data.receive(message);
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

  /** Data arrived: draw the shown frame again if a waiting lane can now, and let a held frame go. */
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
    this.#cadence = cadence(times);
    this.#clock = new PlaybackClock(times, this.#deps.now, this.#resumeAt ?? 0);
    this.#clock.setSpeed(this.#speed);
    this.#failures.clear();
    this.#status = "ready";
    this.#link.settled();
    this.#frameCount = opened.frame_count;
    this.#applyFollowed();
    this.#wait.restart();
    // The reopened clock is paused: tell the presenter's followers where this viewer is.
    if (this.#stoppedByLoss && !this.#followed) this.#intend();
    this.#stoppedByLoss = false;
    this.#sendWant();
    this.#publish();
  }

  /**
   * One display refresh: present the newest due frame, if any, then report the window. Refreshes are asked for only
   * while playing or when something changed or an awaited frame arrived.
   */
  #tick = (): void => {
    this.#cancelFrame = undefined;
    const clock = this.#clock;
    if (!clock || !this.#buffer) return;
    const wasPlaying = clock.playing;
    const presented = clock.tick(this.#source);
    // Playing ended on its own at the last frame: the intent is a pause there, unless it follows a presenter.
    if (wasPlaying && !clock.playing && !this.#followed) this.#intend();
    if (presented !== undefined) {
      this.#draw(presented);
      if (this.#autoplay) this.play();
    }
    this.#sendWant();
    this.#holdForData(clock, presented !== undefined);
    this.#publish();
    if (clock.playing) this.#arm();
  };

  /** A paused clock holding its frame's video but not the lanes' data asks for a refresh when the wait ends. */
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
    this.#clock?.fail(sequenceId);
  }

  /** Sends the window when it differs from the last; the buffer keeps that window. */
  #sendWant(): void {
    const clock = this.#clock;
    const buffer = this.#buffer;
    if (!clock || !buffer || !this.#planner) return;
    const frames = this.#planner.want({ playhead: clock.playhead, playing: clock.playing });
    const want = { ...frames, ...dataWindow(this.#data.sampleBytes(), this.#budget, frames) };
    if (sameWant(this.#lastWant, want)) return;
    this.#lastWant = want;
    buffer.setWindow(want);
    this.#link.send({ type: "Want", ...want });
  }

  /** The socket closed on its own: drop the video and open again where it left off, paused. */
  #lose(): void {
    this.#stoppedByLoss ||= this.#clock?.playing ?? false;
    this.#releaseVideo();
    this.#opened = undefined;
    this.#status = "reconnecting";
    this.#link.retry();
    this.#publish();
  }

  #fail(reason: string, location?: string): void {
    this.#failure = { reason, location };
    this.#status = "failed";
    this.#link.drop();
    this.#releaseVideo();
    this.#publish();
  }

  /** Stops the display loop and drops the clock and held frames, keeping the position. */
  #releaseVideo(): void {
    this.#resumeAt = this.#clock?.playhead ?? this.#resumeAt;
    this.exports.lost();
    this.#cancelFrame?.();
    this.#cancelFrame = undefined;
    this.#clock = undefined;
    this.#times = undefined;
    this.#cadence = null;
    this.#buffer = undefined;
    this.#data.close();
    this.#wait.stop();
    this.#planner = undefined;
    this.#lastWant = undefined;
    this.#failures.clear();
    this.#cached = [];
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
      cadence: this.#cadence,
      cached: this.#cached,
    });
    if (sameView(view, this.#view)) return;
    this.#view = view;
    this.#listeners.forEach((listener) => listener());
  }
}
