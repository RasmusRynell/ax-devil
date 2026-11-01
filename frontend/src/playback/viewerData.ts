/** What a viewer holds of its lanes' data: Sample Items, Frame Maps and catalogs, and what each lane draws from them. */

import { Catalogs } from "./catalogs";
import { FrameMaps } from "./data/FrameMaps";
import { sampleCapBytes } from "./data/dataWindow";
import { SampleStore } from "./data/SampleStore";
import { sampleWindow } from "./data/sampleWindow";
import { windowRange } from "./FrameBuffer";
import { LaneData } from "./laneData";
import type { LaneFrame, Surface } from "./contract";
import type { LaneSet } from "./lanes";
import type { ClientMessage, FrameMapEnvelope, SampleEnvelope, VideoFrame, Want } from "./protocol";

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

  /** Starts holding maps for a video of `frameCount` frames, and samples within a cap sized from the buffer budget. */
  open(frameCount: number, budgetBytes: number): void {
    this.samples = new SampleStore(sampleCapBytes(budgetBytes));
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

  /** The lanes changed: their revisions, the data window and their catalogs are taken up again. */
  refresh(want: Want | undefined): void {
    this.sync();
    this.retain(want);
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

  /** Tells the maps and the sample store which revisions each lane's and source's data must carry. */
  sync(): void {
    this.#lanes.states.forEach((state, lane) => {
      if (state.source === null) return;
      const ready =
        state.status !== "plain" && state.status.status === "Ready" ? state.status : undefined;
      if (ready) this.samples.setRevision(state.source, ready.data_revision);
      this.#maps?.setRevisions(lane, state.policyRevision, ready?.data_revision ?? NOT_READY);
    });
  }

  /**
   * Keeps the maps of the data window of `want`, and the samples the maps held select inside it, most urgent first.
   * The backend sends only the part of the window its authority allows, so the maps are often incomplete; a sample
   * no held map names is dropped all the same, as the backend forgets it when its frames leave the window, and
   * what the cap cannot hold goes least urgent first.
   */
  retain(want: Want | undefined): void {
    const maps = this.#maps;
    if (!want || !maps) return;
    const { playhead, direction } = want;
    const { low, high } = windowRange(
      { playhead, ahead: want.data_ahead, behind: want.data_behind },
      this.#frameCount,
    );
    maps.retain(low, high);
    // The shown lanes of each source; a source without any keeps no sample.
    const shown = new Map<number, number[]>();
    this.#lanes.states.forEach((state, lane) => {
      if (state.source === null) return;
      const lanes = shown.get(state.source) ?? [];
      if (this.#lanes.awaited(lane)) lanes.push(lane);
      shown.set(state.source, lanes);
    });
    for (const [source, lanes] of shown) {
      const cells = lanes.map(() => new Int32Array(high - low + 1));
      lanes.forEach((lane, i) => maps.window(lane, low, cells[i]));
      this.samples.retain(
        source,
        sampleWindow(cells, low, playhead, direction).map(([sample]) => sample),
      );
    }
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
