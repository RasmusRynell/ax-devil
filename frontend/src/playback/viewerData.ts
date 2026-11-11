/** What a viewer holds of its lanes' data: Sample Items, Frame Maps and catalogs, and what each lane draws from them. */

import { Catalogs } from "../lanes/catalogs";
import { FileScope } from "../inspect/fileScope";
import { FrameMaps } from "../stream/data/FrameMaps";
import { SampleStore } from "../stream/data/SampleStore";
import { FrameFields } from "../inspect/frameFields";
import { Inspections } from "../inspect/inspection";
import { LaneCatalogs } from "../lanes/laneCatalog";
import { LaneData, type LaneMatch } from "../lanes/laneData";
import { NextEntry } from "./nextEntry";
import type { LaneFrame, LaneSettings, Surface } from "../lanes/contract";
import { startWith, type LaneSet, type Saved } from "../lanes/lanes";
import type {
  ClientMessage,
  FrameMapEnvelope,
  LaneInfo,
  SampleEnvelope,
  ServerMessage,
  VideoFrame,
} from "../stream/protocol";

import { MATCH_RETAINED } from "../stream/protocol";

type Opened = Extract<ServerMessage, { type: "Opened" }>;

/** The revision of a source that has not said it is ready: no Frame Map run carries it. */
const NOT_READY = -1;

export class ViewerData {
  #applyRequest = 0;
  readonly #lanes: LaneSet;
  readonly #send: (message: ClientMessage) => void;
  readonly #after: (callback: () => void, ms: number) => () => void;
  readonly #changed: () => void;
  samples = new SampleStore(0);
  readonly catalogs: Catalogs;
  readonly inspections: Inspections;
  /** The leaves of the shown frames' objects that a search names. */
  readonly fields: FrameFields;
  /** The lists of the lanes' whole files. */
  readonly files: FileScope;
  /** The entry the backend is asked to prepare next. */
  readonly next: NextEntry;
  readonly #laneCatalogs: LaneCatalogs;
  /** The built-in default catalog, which a lane draws with when its own has failed and it has no other. */
  #fallback = "";
  #maps: FrameMaps | undefined;
  #frameCount = 0;
  #draw: LaneData | undefined;
  /** The lanes that waited for data on the frame last composed. */
  #waiting: number[] = [];
  /** What each lane drew on the frame last composed; `undefined` for a lane that drew nothing. */
  #shown: readonly (LaneFrame | undefined)[] = [];
  /** The samples of the last presented video, retained independently of later matching revisions. */
  #presentation: readonly (LaneFrame | undefined)[] = [];
  /** How each lane's sample was matched to the frame last composed. */
  #matched: readonly (LaneMatch | null)[] = [];

  /** `send` asks the backend for a catalog document, an object's fields or a file's list; `after` times the asking; `changed` hears when the inspector catches up with the playing frames later. */
  constructor(
    lanes: LaneSet,
    send: (message: ClientMessage) => void,
    after: (callback: () => void, ms: number) => () => void,
    changed: () => void = () => {},
  ) {
    this.#lanes = lanes;
    this.#send = send;
    this.#after = after;
    this.#changed = changed;
    this.files = new FileScope(lanes, send, after);
    this.catalogs = new Catalogs((id) => send({ type: "Catalog", id }));
    this.#laneCatalogs = new LaneCatalogs(this.catalogs, () => this.#fallback);
    this.inspections = new Inspections(send);
    this.fields = new FrameFields(send, after);
    this.next = new NextEntry(send);
  }

  /** Validates before copying; newer choices survive the pending read. Watchers use the shared compiled document. */
  async applyToAll(lane: number, reread: boolean): Promise<ClientMessage[]> {
    const before = this.#lanes.states.map((state) => state.settings);
    const source = before[lane];
    if (!source) return [];
    const request = ++this.#applyRequest;
    const current = () =>
      request === this.#applyRequest && this.#lanes.states[lane]?.settings === source;
    try {
      if (reread) {
        if (!(await this.catalogs.validate(source.catalog, current))) return [];
      }
      if (!current()) return [];
      const held = this.catalogs.get(source.catalog);
      if (held.state !== "ready" || !held.catalog || held.problem) return [];
      this.#laneCatalogs.dismissAction(lane);
      return this.#lanes.copyFrom(lane, before);
    } catch (error) {
      if (request === this.#applyRequest && this.#lanes.states[lane]?.settings === source)
        this.#laneCatalogs.refuseApply(
          lane,
          source.catalog,
          error instanceof Error ? error.message : String(error),
        );
      return [];
    }
  }

  get draw(): LaneData | undefined {
    return this.#draw;
  }

  /**
   * Draws `video` with what each lane draws on `frame` (nothing for a lane that waits for its data), and records
   * which lanes wait. Reports whether that changed what the lanes show of themselves.
   */
  present(video: VideoFrame, frame: number, surface: Surface | null, playing = false): boolean {
    const draw = this.#draw;
    if (!draw) return false;
    const waiting: number[] = [];
    const frames = this.#lanes.states.map((_, lane): LaneFrame | undefined => {
      const drawn = draw.at(lane, frame);
      if (drawn.state === "pending") waiting.push(lane);
      return drawn.state === "draw" ? drawn.frame : undefined;
    });
    this.#waiting = waiting;
    this.#presentation = frames;
    this.#matched = this.#lanes.states.map((_, lane) => draw.match(lane, frame));
    return this.#show(video, frames, surface, playing);
  }

  /** Redraws the held presentation after appearance edits, even while future maps are being replaced. */
  redraw(video: VideoFrame, surface: Surface | null): boolean {
    const draw = this.#draw;
    if (!draw) return false;
    const composed = this.#presentation.map((held, lane) =>
      draw.restyle(lane, held, this.#matched[lane]?.kind === MATCH_RETAINED),
    );
    if (composed.some((frame) => frame.state === "pending")) return false;
    return this.#show(
      video,
      composed.map((frame) => (frame.state === "draw" ? frame.frame : undefined)),
      surface,
      false,
    );
  }

  #show(
    video: VideoFrame,
    frames: readonly (LaneFrame | undefined)[],
    surface: Surface | null,
    playing: boolean,
  ): boolean {
    this.#shown = frames;
    surface?.present(video, frames);
    let changed = this.inspections.follow(frames, playing);
    this.inspections.trail(this.#after, this.#changed);
    this.#lanes.states.forEach((_, lane) => {
      changed = this.#lanes.setPending(lane, this.#waiting.includes(lane)) || changed;
    });
    return changed;
  }

  /** What `lane` drew on the frame on screen. */
  shown(lane: number): LaneFrame | undefined {
    return this.#shown[lane];
  }

  /** How `lane` matched a sample to the frame on screen; `null` for a lane without a source or that waited for its data. */
  matched(lane: number): LaneMatch | null {
    return this.#matched[lane] ?? null;
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
    this.#draw = new LaneData(this.#lanes, this.samples, this.#maps, this.#laneCatalogs);
    this.sync();
  }

  /** Drops everything held but the compiled catalogs. */
  close(): void {
    this.#applyRequest++;
    this.#laneCatalogs.clearActions();
    this.#lanes.states.forEach((_, lane) => this.#lanes.setCatalogActionProblem(lane, undefined));
    this.#maps = undefined;
    this.#draw = undefined;
    this.#waiting = [];
    this.#shown = [];
    this.#presentation = [];
    this.#matched = [];
    this.samples.clear();
    this.inspections.suspend();
    this.files.suspend();
    this.next.close();
  }

  /**
   * The backend opened the entry: its lanes open, starting from `prior` settings, else the `personal` default catalog
   * and `saved`; what the old connection was asked is asked again, and lanes that changed forget their catalog.
   */
  reopen(
    opened: Pick<Opened, "lanes" | "lane_defaults" | "catalogs">,
    personal: string | null | undefined,
    prior: readonly LaneSettings[] | undefined,
    saved?: Saved,
  ): void {
    this.#applyRequest++;
    this.#laneCatalogs.clearActions();
    const before = this.#lanes.states;
    const defaults = startWith(opened.lane_defaults, opened.catalogs, personal, saved);
    this.#lanes.open(opened.lanes, defaults, prior).forEach(this.#send);
    this.#fallback = opened.lane_defaults.catalog;
    this.catalogs.open(opened.catalogs);
    this.inspections.reconnect();
    this.fields.reconnect();
    this.files.reopen();
    this.next.reopen();
    opened.lanes.forEach((lane: LaneInfo, index) => {
      if (before[index]?.label !== lane.label || before[index]?.source !== lane.source)
        this.#laneCatalogs.forget(index);
    });
    this.refresh();
  }

  /**
   * A catalog document or the listing of catalogs arrived. Reports whether a lane may look different: the caller
   * draws the shown frame again.
   */
  receiveCatalogs(message: Extract<ServerMessage, { type: "Catalog" | "Catalogs" }>): boolean {
    if (message.type === "Catalogs") this.catalogs.list(message.catalogs);
    else if (
      !this.catalogs.receive(message.id, message.revision, message.document, message.problem)
    )
      return false;
    this.catalogProblems();
    return true;
  }

  /** Takes a message that only changes the data held (an inspector answer, the data window, evictions, a lane's problem, file lists); false for any other. */
  receive(message: ServerMessage): boolean {
    if (message.type === "Inspected") this.inspections.receive(message);
    else if (message.type === "InspectedFields") this.fields.receive(message);
    else if (message.type === "DataWindow") this.window(message.first, message.last);
    else if (message.type === "Evict") this.evict(message);
    else if (message.type === "LaneProblem") this.#lanes.setProblem(message.lane, message.reason);
    else if (message.type === "File") this.files.receive(message);
    else return false;
    return true;
  }

  /** Whether a run of a lane's map lies inside the video. */
  fits(map: FrameMapEnvelope): boolean {
    return map.first + map.samples.length <= this.#frameCount;
  }

  /** Keeps a Sample Item; an item that cannot be read is not kept and its reason is returned. */
  addSample({ source, dataRevision, sample, item, timeUs, frame }: SampleEnvelope): string | null {
    return this.samples.add(source, dataRevision, sample, item, { timeUs, frame });
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
    this.files.refresh();
  }

  /**
   * What one frame's items take, at most: the mean item size of every source a shown lane draws, each counted once
   * however many lanes show it; null without such a lane.
   */
  sampleBytes(): number | null {
    const bySource = new Map<number, number>();
    this.#lanes.states.forEach((state, lane) => {
      const ready = this.#lanes.awaited(lane);
      if (ready && state.source !== null)
        bySource.set(
          state.source,
          Math.max(bySource.get(state.source) ?? 0, ready.mean_sample_bytes),
        );
    });
    return bySource.size === 0
      ? null
      : [...bySource.values()].reduce((sum, bytes) => sum + bytes, 0);
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

  /** Dismisses an Apply refusal without touching backend errors. */
  dismissCatalogAction(lane: number): void {
    this.#laneCatalogs.dismissAction(lane);
    this.#lanes.setCatalogActionProblem(lane, undefined);
  }

  /** Asks the backend for the catalog `lane` chose again; what it answers is compiled anew. */
  reloadCatalog(lane: number): void {
    this.catalogs.reload(this.#lanes.states[lane].settings.catalog);
  }

  /** Why each lane's catalog is not the chosen one and what the catalog it draws with offers, asking for catalogs not yet asked for. */
  catalogProblems(): void {
    this.#lanes.states.forEach((state, lane) => {
      // An export names the catalog of every lane, plain ones too: the lane draws nothing, but the document is held.
      this.#lanes.setCatalogRevision(lane, this.catalogs.compiledRevision(state.settings.catalog));
      if (state.source === null) {
        this.catalogs.get(state.settings.catalog);
        return;
      }
      const { catalog, problem } = this.#laneCatalogs.resolve(lane, state.settings.catalog);
      this.#lanes.setCatalogProblem(lane, problem);
      this.#lanes.setCatalogActionProblem(
        lane,
        this.#laneCatalogs.actionProblem(lane, state.settings.catalog),
      );
      const held = this.#lanes.states[lane].look;
      const same = held?.themes === catalog?.themes && held?.features === catalog?.features;
      if (!same)
        this.#lanes.setLook(
          lane,
          catalog && { themes: catalog.themes, features: catalog.features },
        );
    });
  }
}
