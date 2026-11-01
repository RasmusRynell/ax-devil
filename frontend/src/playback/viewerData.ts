/** What a viewer holds of its lanes' data: Sample Items, Frame Maps and catalogs, and what each lane draws from them. */

import { Catalogs } from "./catalogs";
import { FrameMaps } from "./data/FrameMaps";
import { SampleStore } from "./data/SampleStore";
import { LaneData } from "./laneData";
import type { LaneFrame, Surface } from "./contract";
import type { LaneSet } from "./lanes";
import type {
  ClientMessage,
  FrameMapEnvelope,
  SampleEnvelope,
  ServerMessage,
  VideoFrame,
} from "./protocol";

/** The revision of a source that has not said it is ready: no Frame Map run carries it. */
const NOT_READY = -1;

export class ViewerData {
  readonly #lanes: LaneSet;
  samples = new SampleStore(0);
  readonly catalogs: Catalogs;
  #maps: FrameMaps | undefined;
  #frameCount = 0;
  #draw: LaneData | undefined;
  /** The lanes that waited for data on the frame last composed. */
  #waiting: number[] = [];

  /** `send` asks the backend for a catalog document. */
  constructor(lanes: LaneSet, send: (message: ClientMessage) => void) {
    this.#lanes = lanes;
    this.catalogs = new Catalogs((id) => send({ type: "Catalog", id }));
  }

  get draw(): LaneData | undefined {
    return this.#draw;
  }

  /**
   * Draws `video` with what each lane draws on `frame` (nothing for a lane that waits for its data), and records
   * which lanes wait. Reports whether that changed what the lanes show of themselves.
   */
  present(video: VideoFrame, frame: number, surface: Surface | null): boolean {
    const draw = this.#draw;
    if (!draw) return false;
    const waiting: number[] = [];
    const frames = this.#lanes.states.map((_, lane): LaneFrame | undefined => {
      const drawn = draw.at(lane, frame);
      if (drawn.state === "pending") waiting.push(lane);
      return drawn.state === "draw" ? drawn.frame : undefined;
    });
    this.#waiting = waiting;
    surface?.present(video, frames);
    let changed = false;
    this.#lanes.states.forEach((_, lane) => {
      changed = this.#lanes.setPending(lane, waiting.includes(lane)) || changed;
    });
    return changed;
  }

  /** Whether any lane that waited for the data of the frame last composed no longer does. */
  unblocked(frame: number): boolean {
    return this.#waiting.some((lane) => this.#draw?.at(lane, frame).state !== "pending");
  }

  /** Starts holding maps for a video of `frameCount` frames, and at most `dataBytes` of samples, as stated to the backend. */
  open(frameCount: number, dataBytes: number): void {
    this.samples = new SampleStore(dataBytes);
    this.#frameCount = frameCount;
    this.#maps = new FrameMaps(frameCount);
    this.#draw = new LaneData(this.#lanes, this.samples, this.#maps, this.catalogs);
    this.sync();
  }

  /** Drops everything held but the compiled catalogs. */
  close(): void {
    this.#maps = undefined;
    this.#draw = undefined;
    this.#waiting = [];
    this.samples.clear();
  }

  /** A new connection: requests the old one never answered are asked again. */
  reconnect(): void {
    this.catalogs.reconnect();
  }

  /** Whether a run of a lane's map lies inside the video. */
  fits(map: FrameMapEnvelope): boolean {
    return map.first + map.samples.length <= this.#frameCount;
  }

  /** Keeps a Sample Item; an item that cannot be read is not kept and its reason is returned. */
  addSample({ source, dataRevision, sample, item }: SampleEnvelope): string | null {
    return this.samples.add(source, dataRevision, sample, item);
  }

  /** Keeps a run of a lane's map; reports whether it was kept. */
  addMap(map: FrameMapEnvelope): boolean {
    const { lane, policyRevision, dataRevision, first, samples, kinds } = map;
    return this.#maps?.add(lane, policyRevision, dataRevision, first, samples, kinds) ?? false;
  }

  /** Drops the samples the backend says it no longer counts as held. */
  evict({ source, data_revision, samples }: Extract<ServerMessage, { type: "Evict" }>): void {
    this.samples.evict(source, data_revision, samples);
  }

  /** The backend applied the data window `first..last`: Frame Map cells outside it are dropped, and later runs are cut to it. */
  window(first: number, last: number): void {
    this.#maps?.retain(first, last);
  }

  /** The lanes changed: their revisions and catalogs are taken up again. */
  refresh(): void {
    this.sync();
    this.catalogProblems();
  }

  /** What one frame's items take, at most: the mean item size of every shown lane's source; null without such a lane. */
  sampleBytes(): number | null {
    let bytes: number | null = null;
    this.#lanes.states.forEach((_, lane) => {
      const ready = this.#lanes.awaited(lane);
      if (ready) bytes = (bytes ?? 0) + ready.mean_sample_bytes;
    });
    return bytes;
  }

  /**
   * Tells the maps and the sample store which revisions each lane's and source's data must carry. A source that
   * failed holds no sample, as the backend forgets what it sent; one that is loading again keeps its samples until
   * the next revision is ready.
   */
  sync(): void {
    this.#lanes.states.forEach((state, lane) => {
      if (state.source === null) return;
      const ready =
        state.status !== "plain" && state.status.status === "Ready" ? state.status : undefined;
      if (ready) this.samples.setRevision(state.source, ready.data_revision);
      else if (state.status !== "plain" && state.status.status === "Failed")
        this.samples.setRevision(state.source, NOT_READY);
      this.#maps?.setRevisions(lane, state.policyRevision, ready?.data_revision ?? NOT_READY);
    });
  }

  /** Why each lane's catalog is not the chosen one, asking for the catalogs not yet asked for. */
  catalogProblems(): void {
    this.#lanes.states.forEach((state, lane) => {
      if (state.source === null) return;
      const entry = this.catalogs.get(state.settings.catalog);
      this.#lanes.setCatalogProblem(lane, entry.state === "ready" ? entry.problem : undefined);
    });
  }
}
