/**
 * What the inspector shows of the selected object. The selection is the viewer's own state and survives seeking and
 * playing by object id. The summary comes at once from the Sample Item the lane shows; every other field comes from
 * one `Inspect` request per (source, data revision, sample, object): asking for a newer sample supersedes a request
 * still out (the backend keeps only the newest), and the last 16 replies are kept.
 */

import type { InspectionView, LaneFrame, Selection, Summary } from "./contract";
import type { ClientMessage, FieldWire, InspectionWire, ServerMessage } from "./protocol";

/** How many replies are kept, so that stepping back and forth asks the backend once per frame. */
const CACHE_SIZE = 16;

const encoder = new TextEncoder();

type Reply = Extract<ServerMessage, { type: "Inspected" }>;

/** The sample a selection is looked up in: the frame its lane draws. */
type Shown = { frame: LaneFrame; summary: Summary } | "absent";

const keyOf = (source: number, revision: number, sample: number, entity: string): string =>
  `${source}:${revision}:${sample}:${entity}`;

/** What the Sample Item says of the object, or null when it has no such object. */
function summarize(frame: LaneFrame, entity: string): Summary | null {
  const { columns } = frame;
  const bytes = encoder.encode(entity);
  for (let row = 0; row < columns.rows; row++) {
    if (!columns.strings.equals(columns.entityId[row], bytes)) continue;
    const classId = columns.classId[row];
    const number = (value: number) => (Number.isNaN(value) ? null : value);
    return {
      entity,
      className: classId < 0 ? null : columns.strings.string(classId),
      score: number(columns.score[row]),
      confidence: number(columns.confidence[row]),
    };
  }
  return null;
}

const sameSummary = (a: Summary, b: Summary): boolean =>
  a.entity === b.entity &&
  a.className === b.className &&
  a.score === b.score &&
  a.confidence === b.confidence;

export class Inspections {
  readonly #send: (message: ClientMessage) => void;
  #selection: Selection = null;
  #frames: readonly (LaneFrame | undefined)[] = [];
  /** The newest data revision each source was drawn with: a reply of an older one is dropped. */
  readonly #revisions = new Map<number, number>();
  /** The replies kept, oldest first. Unavailable is not kept: it says nothing about the sample. */
  readonly #cache = new Map<string, InspectionWire>();
  #inflight: string | null = null;
  /** The sample the backend last said it would not answer for. */
  #refused: string | null = null;
  /** The fields last shown, which stay until the shown sample's reply replaces them. */
  #fields: { entity: string; fields: readonly FieldWire[]; truncated: boolean } | null = null;
  /** The sample the view was last made for, so that drawing the same one again costs nothing. */
  #at: { selection: Selection; sample: string | null; columns: unknown } | null = null;
  #view: InspectionView = { state: "none" };

  /** `send` asks the backend for an object's fields. */
  constructor(send: (message: ClientMessage) => void) {
    this.#send = send;
  }

  /** What the inspector shows; the same object until something in it changed. */
  get view(): InspectionView {
    return this.#view;
  }

  /** Selects an object, or clears the selection with `null`. Reports whether the view changed. */
  select(selection: Selection): boolean {
    if (selection?.entity !== this.#selection?.entity) this.#fields = null;
    this.#selection = selection;
    this.#at = null;
    return this.#update();
  }

  /** The lanes drew these frames: follows the selection to the shown samples. Reports whether the view changed. */
  follow(frames: readonly (LaneFrame | undefined)[]): boolean {
    this.#frames = frames;
    for (const frame of frames) if (frame) this.#revisions.set(frame.source, frame.dataRevision);
    const at = this.#sampleAt();
    const held = this.#at;
    if (
      held?.selection === this.#selection &&
      held.sample === at.sample &&
      held.columns === at.columns
    )
      return false;
    return this.#update();
  }

  /** The backend's answer to a request for fields. Reports whether the view changed. */
  receive(message: Reply): boolean {
    const key = keyOf(message.source, message.data_revision, message.sample, message.entity);
    if (key === this.#inflight) this.#inflight = null;
    if (message.data_revision === this.#revisions.get(message.source)) {
      if (message.inspection.state === "Unavailable") this.#refused = key;
      else this.#remember(key, message.inspection);
    }
    return this.#update();
  }

  /** A new connection: what was asked of the old one is asked again. */
  reconnect(): void {
    this.#inflight = null;
    this.#refused = null;
    this.#cache.clear();
    this.#update();
  }

  /** Which sample the selection's lane shows. */
  #sampleAt(): { sample: string | null; columns: unknown } {
    const frame = this.#selection && this.#frames[this.#selection.lane];
    return frame
      ? {
          sample: keyOf(frame.source, frame.dataRevision, frame.sample, ""),
          columns: frame.columns,
        }
      : { sample: null, columns: null };
  }

  #remember(key: string, inspection: InspectionWire): void {
    this.#cache.delete(key);
    this.#cache.set(key, inspection);
    if (this.#cache.size > CACHE_SIZE)
      this.#cache.delete(this.#cache.keys().next().value as string);
  }

  /** What the selection's lane shows of the object. */
  #shown(selection: NonNullable<Selection>): Shown {
    const frame = this.#frames[selection.lane];
    const summary =
      frame && frame.source === selection.source && summarize(frame, selection.entity);
    return frame && summary ? { frame, summary } : "absent";
  }

  /**
   * Asks for the shown object's fields unless they are held, refused, or already asked for. A request for another
   * sample is superseded, not awaited: the backend keeps only the newest, so a reply it drops costs nothing.
   */
  #ask(frame: LaneFrame, entity: string): void {
    const key = keyOf(frame.source, frame.dataRevision, frame.sample, entity);
    if (this.#inflight === key || this.#refused === key || this.#cache.has(key)) return;
    this.#inflight = key;
    this.#send({
      type: "Inspect",
      source: frame.source,
      data_revision: frame.dataRevision,
      sample: frame.sample,
      entity,
    });
  }

  #update(): boolean {
    const selection = this.#selection;
    const next = selection ? this.#build(selection) : ({ state: "none" } as const);
    this.#at = { selection, ...this.#sampleAt() };
    const before = this.#view;
    const same =
      before.state === next.state &&
      (before.state === "none" ||
        (next.state !== "none" &&
          before.selection.lane === next.selection.lane &&
          before.selection.source === next.selection.source &&
          before.selection.entity === next.selection.entity &&
          (before.state === "absent" ||
            (next.state !== "absent" &&
              sameSummary(before.summary, next.summary) &&
              before.fields === next.fields &&
              before.truncated === next.truncated))));
    if (same) return false;
    this.#view = next;
    return true;
  }

  #build(selection: NonNullable<Selection>): InspectionView {
    const shown = this.#shown(selection);
    if (shown === "absent") return { state: "absent", selection };
    const { frame, summary } = shown;
    const key = keyOf(frame.source, frame.dataRevision, frame.sample, selection.entity);
    const reply = this.#cache.get(key);
    if (reply?.state === "Found")
      this.#fields = { entity: selection.entity, fields: reply.fields, truncated: reply.truncated };
    if (reply?.state === "Absent") return { state: "absent", selection };
    this.#ask(frame, selection.entity);
    const held = this.#fields;
    return {
      state: reply ? "ready" : this.#refused === key ? "unavailable" : "loading",
      selection,
      summary,
      fields: held?.fields ?? NO_FIELDS,
      truncated: held?.truncated ?? false,
    };
  }
}

const NO_FIELDS: readonly FieldWire[] = [];
