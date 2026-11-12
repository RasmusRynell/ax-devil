/**
 * A viewer's playback. Playback never waits for lane data; seeking waits briefly. Time, display refresh and socket
 * are injected; the UI reads `getSnapshot`.
 */

import type { ErrorWire } from "../generated/api";
import { reasonOf } from "../client/errors";
import { ExportJobs } from "../export/jobs";
import {
  PlaybackClock,
  clampSpeed,
  steppedSpeed,
  type ClockIntent,
  type FrameSource,
} from "../stream/clock";
import type { LaneControl, LaneFrame, LaneSettings, Selection, Surface } from "../lanes/contract";
import { dataWindow, sampleBudgetBytes } from "../stream/data/dataWindow";
import { FrameFailures } from "../stream/failures";
import { followClock } from "../stream/follow";
import { FrameBuffer, windowSize } from "../stream/FrameBuffer";
import { DataWait, displaySource } from "../stream/frameSource";
import { WantPlanner, sameWant } from "../stream/direction";
import type { LaneMatch } from "../lanes/laneData";
import { LaneSet } from "../lanes/lanes";
import { Link } from "./link";
import {
  PLAYBACK_PROTOCOL,
  type Envelope,
  type ServerMessage,
  type VideoFrame,
  type Want,
} from "../stream/protocol";
import { BUFFER_BUDGET_BYTES, ENDED, type ResumeState, type StreamDeps } from "./streamDeps";
import { INITIAL_VIEW, buildView, sameView, type PlaybackView } from "./view";
import { ViewerData } from "./viewerData";
import type { Incoming } from "../stream/wire";

export type { Surface } from "../lanes/contract";

export class PlaybackStream implements LaneControl {
  readonly #deps: StreamDeps;
  /** The most frame bytes the viewer may hold: what `Open.data_bytes` and samples keep to. */
  readonly #ceiling: number;
  /** What it may hold now: its share of the window's budget, which sizes the windows. */
  #budget: number;
  readonly #listeners = new Set<() => void>();
  readonly #failures = new FrameFailures();
  #view: PlaybackView = INITIAL_VIEW;
  readonly #lanes = new LaneSet((slot, policy) => this.#deps.onPolicy?.(slot, policy));
  readonly #data = new ViewerData(
    this.#lanes,
    (message) => this.#link.send(message),
    (callback, ms) => this.#deps.after(callback, ms),
    () => this.#publish(),
  );
  /** A paused clock waits briefly for lane data after a seek or step. */
  readonly #wait = new DataWait(
    () => this.#deps.now(),
    (callback, ms) => this.#deps.after(callback, ms),
    () => this.#arm(),
  );
  /** What the clock reads: the video held and the lanes' data or the wait over. */
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
  /** The presenter's clock last followed, and how to read the backend's time. */
  #followed: { intent: ClockIntent; serverNowUs: () => number } | undefined;
  #planner: WantPlanner | undefined;
  #lastWant: Want | undefined;
  #speed = 1;
  /** The frame to reopen on. */
  #resumeAt: number | undefined;
  /** An ordinary open plays from its first frame once shown, unless the viewer acts first. */
  #autoplay: boolean;
  #wasPlaying = false;
  /** A lost connection took a running clock; the reopened one is paused. */
  #stoppedByLoss = false;
  #cancelFrame: (() => void) | undefined;
  #frameCount = 0;
  #failure: PlaybackView["failure"];
  #status: PlaybackView["status"] = "connecting";
  /** The frames the backend holds decoded. */
  #cached: PlaybackView["cached"] = [];

  constructor(deps: StreamDeps) {
    this.#deps = deps;
    this.#ceiling = this.#budget = deps.budgetBytes ?? BUFFER_BUDGET_BYTES;
    this.#resumeAt = deps.resume?.frame;
    this.#autoplay = deps.autoplay === true;
    this.#speed = clampSpeed(deps.resume?.speed ?? 1);
    this.#link = new Link(deps, {
      open: () => ({
        type: "Open",
        start_frame: this.#resumeAt,
        data_bytes: sampleBudgetBytes(this.#ceiling),
      }),
      incoming: (incoming) => this.#onIncoming(incoming),
      refused: (reason) => this.#fail(reason),
      lost: () => this.#lose(),
    });
  }

  /** Where the next stream should open, and whether it played; kept after the video goes. */
  resumeState(): ResumeState & { playing: boolean } {
    const playing = (this.#clock?.playing ?? this.#wasPlaying) || this.#autoplay;
    return { frame: this.#clock?.playhead ?? this.#resumeAt ?? 0, speed: this.#speed, playing };
  }

  /** Whether an open still owes its autoplay. */
  get autoplayPending(): boolean {
    return this.#autoplay;
  }

  /** Connects and asks for the video. */
  start(): void {
    this.#link.connect();
  }

  /** Ends the stream: tells the backend, stops the display and drops every frame. */
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
  readonly files = this.#data.files;

  /** The leaves of the shown frames' objects at the paths a search names. */
  readonly fields = this.#data.fields;

  /** The entry the backend is asked to prepare in the background. */
  readonly next = this.#data.next;

  /** What `lane` drew on the frame on screen, which Frame scope lists. */
  shown(lane: number): LaneFrame | undefined {
    return this.#data.shown(lane);
  }

  /** How `lane` matched a sample to the frame on screen. */
  matched(lane: number): LaneMatch | null {
    return this.#data.matched(lane);
  }

  /** Changes the frame bytes held: the window resizes to fit, and the backend is asked for it. */
  setBudget(bytes: number): void {
    this.#budget = bytes;
    if (!this.#opened || !this.#buffer) return;
    this.#buffer.setBudget(bytes);
    this.#planner?.resize(windowSize(bytes, this.#opened.frame_bytes));
    this.#sendWant();
  }

  /** Sets where frames are drawn; `null` stops drawing. */
  setSurface(surface: Surface | null): void {
    this.#surface = surface;
  }

  /** Draws the shown frame again, for a surface that lost its picture or a pause. */
  represent(): void {
    const shown = this.#clock?.info();
    if (shown) this.#draw(shown.sequenceId);
  }

  /** Changes matching through LanePolicy; applies style to held samples immediately. */
  setLane(lane: number, change: Partial<LaneSettings>): void {
    const message = this.#lanes.set(lane, change);
    if (message) this.#link.send(message);
    this.#data.refresh();
    this.represent();
    this.#changed();
  }

  /** Applies a validated lane to the others. */
  async applyToAll(lane: number, reread = true): Promise<void> {
    (await this.#data.applyToAll(lane, reread)).forEach((message) => this.#link.send(message));
    this.#data.refresh();
    this.represent();
    this.#changed();
  }

  /** Dismisses the lane's Apply refusal. */
  dismissCatalogAction(lane: number): void {
    this.#data.dismissCatalogAction(lane);
    this.#changed();
  }

  /** Asks the backend for the catalog `lane` chose again; it is compiled when it arrives. */
  reloadCatalog(lane: number): void {
    this.#data.reloadCatalog(lane);
  }

  /** Seeds for the next entry's lanes, by position. */
  laneSettings(): readonly LaneSettings[] {
    return this.#lanes.states.map((lane) => lane.settings);
  }

  /** Selects an object (`null`: none) for the inspector, not for the backend. */
  select(selection: Selection): void {
    if (this.#data.inspections.select(selection)) this.#publish();
  }

  /** Updates visibility and the pins resolved by the inspection owner. */
  showInspector(shown: boolean, pins?: readonly import("./fields").FieldId[]): void {
    const changed = this.#data.inspections.setVisible(shown);
    if ((pins ? this.#data.inspections.setPins(pins) : false) || changed) this.#publish();
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
    this.represent();
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

  /** Sets the speed, in hundredths within 0.01 to 10; kept across a reconnection. */
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

  /** Follows a presenter: puts the clock where the presenter's is by now (`serverNowUs` reads the backend's time) and at its speed. */
  follow(intent: ClockIntent, serverNowUs: () => number, own = false): void {
    this.#autoplay = false;
    this.#followed = { intent, serverNowUs };
    this.#data.files.setOwner(own);
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
        this.#deps.savedDefaults?.(),
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
      return this.#fail(message);
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

  /** Data arrived: redraw the shown frame if a waiting lane can now, and let a held frame go. */
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
    if (held && this.#data.present(held, sequenceId, this.#surface, this.#clock?.playing))
      this.#publish();
  }

  #ready(opened: Extract<ServerMessage, { type: "Opened" }>, times: Float64Array): void {
    if (times.length === 0 || times.length !== opened.frame_count || opened.frame_bytes <= 0)
      return this.#fail("The backend described the video inconsistently", "playback");
    this.#buffer = new FrameBuffer<VideoFrame>(this.#budget, times.length);
    this.#data.open(times.length, sampleBudgetBytes(this.#ceiling));
    this.#planner = new WantPlanner(windowSize(this.#budget, opened.frame_bytes));
    this.#times = times;
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
   * One display refresh: present the newest due frame, if any, then report the window. Asked for only while playing,
   * after a change or awaited frame.
   */
  #tick = (): void => {
    this.#cancelFrame = undefined;
    const clock = this.#clock;
    if (!clock || !this.#buffer) return;
    const wasPlaying = clock.playing;
    const presented = clock.tick(this.#source);
    // Playing ended by itself at the last frame: a pause is the intent, unless it follows a presenter.
    if (wasPlaying && !clock.playing && !this.#followed) this.#intend();
    if (presented !== undefined) {
      this.#draw(presented);
      // At the last frame there is nothing to play: it stays and says so.
      const spent = this.#resumeAt !== undefined && clock.playhead >= this.#frameCount - 1;
      if (this.#autoplay && spent) this.pause();
      else if (this.#autoplay) this.play();
    }
    this.#sendWant();
    this.#holdForData(clock, presented !== undefined);
    this.#publish();
    if (clock.playing) this.#arm();
  };

  /** A paused clock with its video but not the lanes' data asks for a refresh when the wait ends. */
  #holdForData(clock: PlaybackClock, presented: boolean): void {
    if (clock.playing || presented || clock.info()?.sequenceId === clock.playhead) return;
    if (this.#buffer?.has(clock.playhead)) this.#wait.hold();
  }

  #arm(): void {
    if (this.#clock && !this.#cancelFrame) this.#cancelFrame = this.#deps.frame(this.#tick);
  }

  /** The user moved something: show it, and report the window at the next refresh. */
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

  /** The socket closed on its own: drop the video and reopen where it left off, paused. */
  #lose(): void {
    this.#stoppedByLoss ||= this.#clock?.playing ?? false;
    this.#releaseVideo();
    this.#opened = undefined;
    this.#status = "reconnecting";
    this.#link.retry();
    this.#publish();
  }

  #fail(error: string | ErrorWire, location?: string): void {
    // A running clock stops with the viewer, unless a presenter's.
    if (this.#clock?.playing && !this.#followed)
      this.#deps.onIntent?.({ frame: this.#clock.playhead, playing: false, speed: this.#speed });
    this.#failure =
      typeof error === "string" ? { code: "Internal", params: {}, reason: error, location } : error;
    this.#status = "failed";
    this.#link.drop();
    this.#releaseVideo();
    this.#publish();
  }

  /** Stops the display loop and drops the clock and held frames, keeping the position. */
  #releaseVideo(): void {
    this.#wasPlaying = this.resumeState().playing;
    this.#resumeAt = this.#clock?.playhead ?? this.#resumeAt;
    this.exports.lost();
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
    this.#cached = [];
  }

  #publish(): void {
    const view = buildView({
      status: this.#status,
      failure: this.#failure,
      frameCount: this.#frameCount,
      speed: this.#speed,
      clock: this.#clock,
      failures: this.#failures,
      lanes: this.#lanes.states,
      catalogs: this.#data.catalogs.listing,
      opened: this.#opened,
      inspection: this.#data.inspections.view,
      cached: this.#cached,
    });
    if (sameView(view, this.#view)) return;
    this.#view = view;
    this.#listeners.forEach((listener) => listener());
  }
}
