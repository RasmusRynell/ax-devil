/**
 * What the inspector shows of the selected object. The selection is the viewer's own state and survives seeking and
 * playing by object id. The summary comes at once from the Sample Item the lane shows; every other field comes from
 * one `Inspect` request per (source, data revision, sample, object): asking for a newer sample supersedes a request
 * still out (the backend keeps only the newest), and the last 16 replies are kept. A request whose reply never
 * comes (the backend may drop one) is asked again when its sample is wanted again, or after RESEND_AFTER draws.
 */

import type { InspectionView, LaneFrame, Selection, Summary } from "./contract";
import type { ClientMessage, FieldWire, InspectionWire, ServerMessage } from "./protocol";

/** How many replies are kept, so that stepping back and forth asks the backend once per frame. */
const CACHE_SIZE = 16;

/** How many draws a request may stay unanswered while its sample is shown before it is asked again. */
const RESEND_AFTER = 120;

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
  /** The request out for the shown sample, and the draw it was sent at. */
  #wanted: string | null = null;
  #inflight: { key: string; at: number } | null = null;
  /**
   * The sample, as held by these columns, the backend said it would not answer for. It is forgotten when another
   * sample is wanted, or the sample is held anew, so that Unavailable is never more than a pause in asking.
   */
  #refused: { key: string; columns: unknown } | null = null;
  /** Draws so far, which time how long a request has been out. */
  #draws = 0;
  /** Whether a connection opened and no frame of its data has been drawn yet: nothing is asked until one is. */
  #awaiting = false;
  /** The fields last shown, which stay until the shown sample's reply replaces them. */
  #fields: {
    source: number;
    entity: string;
    fields: readonly FieldWire[];
    truncated: boolean;
  } | null = null;
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
    if (
      selection?.entity !== this.#selection?.entity ||
      selection?.source !== this.#selection?.source
    )
      this.#fields = null;
    this.#selection = selection;
    this.#at = null;
    return this.#update();
  }

  /** The lanes drew these frames: follows the selection to the shown samples. Reports whether the view changed. */
  follow(frames: readonly (LaneFrame | undefined)[]): boolean {
    this.#frames = frames;
    const resumed = this.#awaiting;
    this.#awaiting = false;
    this.#draws++;
    for (const frame of frames) if (frame) this.#revisions.set(frame.source, frame.dataRevision);
    const at = this.#sampleAt();
    const held = this.#at;
    if (
      held?.selection === this.#selection &&
      held.sample === at.sample &&
      held.columns === at.columns &&
      !resumed &&
      !this.#overdue()
    )
      return false;
    return this.#update();
  }

  /** The backend's answer to a request for fields. Reports whether the view changed. */
  receive(message: Reply): boolean {
    const key = keyOf(message.source, message.data_revision, message.sample, message.entity);
    if (key === this.#inflight?.key) this.#inflight = null;
    if (message.data_revision === this.#revisions.get(message.source)) {
      if (message.inspection.state === "Unavailable")
        this.#refused = { key, columns: this.#sampleAt().columns };
      else this.#remember(key, message.inspection);
    }
    return this.#update();
  }

  /**
   * A new connection: nothing of the old one holds. The selection is asked about again, but only once a frame of
   * the new connection's data has been drawn, as the backend answers Unavailable for a sample it has not yet seen
   * the viewer hold.
   */
  reconnect(): void {
    this.#inflight = null;
    this.#refused = null;
    this.#awaiting = true;
    this.#revisions.clear();
    this.#cache.clear();
    this.#update();
  }

  /** Whether the request out for the shown sample has waited too long for its reply. */
  #overdue(): boolean {
    const wanted = this.#inflight;
    return wanted !== null && this.#draws - wanted.at > RESEND_AFTER;
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
   * Asks for the shown object's fields unless they are held, refused for this very sample, or recently asked for.
   * A request for another sample is superseded, not awaited: the backend keeps only the newest, and a request it
   * dropped is asked again if its sample is shown again.
   */
  #ask(frame: LaneFrame, entity: string): void {
    const key = keyOf(frame.source, frame.dataRevision, frame.sample, entity);
    const refused = this.#refused;
    if (this.#awaiting || this.#cache.has(key)) return;
    if (refused?.key === key && refused.columns === frame.columns) return;
    if (this.#inflight?.key === key && !this.#overdue()) return;
    this.#inflight = { key, at: this.#draws };
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
    this.#wanted = null;
    const next = selection ? this.#build(selection) : ({ state: "none" } as const);
    // Asked for or refused, and no longer the shown sample: asked again if it is shown again.
    if (this.#inflight && this.#inflight.key !== this.#wanted) this.#inflight = null;
    if (this.#refused && this.#refused.key !== this.#wanted) this.#refused = null;
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
    this.#wanted = key;
    const reply = this.#cache.get(key);
    if (reply?.state === "Found")
      this.#fields = {
        source: selection.source,
        entity: selection.entity,
        fields: reply.fields,
        truncated: reply.truncated,
      };
    if (reply?.state === "Absent") return { state: "absent", selection };
    this.#ask(frame, selection.entity);
    const held = this.#fields;
    return {
      state: reply ? "ready" : this.#refused?.key === key ? "unavailable" : "loading",
      selection,
      summary,
      fields: held?.fields ?? NO_FIELDS,
      truncated: held?.truncated ?? false,
    };
  }
}

const NO_FIELDS: readonly FieldWire[] = [];
