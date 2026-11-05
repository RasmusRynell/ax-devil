/**
 * What the inspector shows of the selected object. The selection is the viewer's own state and survives seeking and
 * playing by object id. The summary comes at once from the Sample Item the lane shows; every other field comes from
 * one `Inspect` request per (source, data revision, sample, object): asking for a newer sample supersedes a request
 * still out (the backend keeps only the newest), and the last 16 replies are kept. A request whose reply never
 * comes (the backend may drop one) is asked again when its sample is wanted again, or after RESEND_AFTER draws.
 * Nothing is asked while no inspector page shows; the selection is kept and asked about when one does. The fields
 * shown are always those of the sample the summary is of: while its reply is out, there are none.
 */

import { MOTION_STATES } from "../present/columns";
import { maskKeeps } from "../present/filter/filter";
import type { InspectionView, LaneFrame, Selection, Summary } from "./contract";
import type { ClientMessage, FieldWire, InspectionWire, ServerMessage } from "./protocol";

/** How many replies are kept, so that stepping back and forth asks the backend once per frame. */
const CACHE_SIZE = 16;

/** How many draws a request may stay unanswered while its sample is shown before it is asked again. */
const RESEND_AFTER = 120;

const encoder = new TextEncoder();

type Reply = Extract<ServerMessage, { type: "Inspected" }>;

/** The sample a selection is looked up in: the frame its lane draws, or why the object is not shown in it. */
type Shown = { frame: LaneFrame; summary: Summary } | "gone" | "filtered";

const keyOf = (source: number, revision: number, sample: number, entity: string): string =>
  `${source}:${revision}:${sample}:${entity}`;

/**
 * What the Sample Item says of the object; null when it has no such object, "filtered" when the lane's class toggles
 * or frame filter keep it from being shown (the same test the lane's rows are kept by).
 */
function summarize(frame: LaneFrame, entity: string): Summary | "filtered" | null {
  const { columns } = frame;
  const bytes = encoder.encode(entity);
  for (let row = 0; row < columns.rows; row++) {
    if (!columns.strings.equals(columns.entityId[row], bytes)) continue;
    const test = frame.filter?.test ?? null;
    frame.filter?.ctx?.bind(columns);
    if (
      !maskKeeps(columns.classMask, row * columns.maskWords, frame.classMask) ||
      (test !== null && test(row) !== 1)
    )
      return "filtered";
    const classId = columns.classId[row];
    const motion = columns.motion[row];
    const number = (value: number) => (Number.isNaN(value) ? null : value);
    return {
      entity,
      className: classId < 0 ? null : columns.strings.string(classId),
      score: number(columns.score[row]),
      confidence: number(columns.confidence[row]),
      motion: motion === 0 ? null : MOTION_STATES[motion - 1],
    };
  }
  return null;
}

const sameSummary = (a: Summary, b: Summary): boolean =>
  a.entity === b.entity &&
  a.className === b.className &&
  a.score === b.score &&
  a.confidence === b.confidence &&
  a.motion === b.motion;

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
  /** Whether an inspector page shows the selection: only then are its fields asked for. */
  #visible = true;
  /** Whether a connection opened and no frame of its data has been drawn yet: nothing is asked until one is. */
  #awaiting = false;
  /** The summary last shown of the selected object, which an absent view keeps showing. */
  #last: Summary | null = null;
  /** The sample the view was last made for, so that drawing the same one again costs nothing. */
  #at: {
    selection: Selection;
    sample: string | null;
    columns: unknown;
    style: number | null;
  } | null = null;
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
    ) {
      this.#last = null;
    }
    this.#selection = selection;
    this.#at = null;
    return this.#update();
  }

  /** Tells whether an inspector page shows. Showing one asks for the selection's fields at once. Reports whether the view changed. */
  setVisible(visible: boolean): boolean {
    if (visible === this.#visible) return false;
    this.#visible = visible;
    return visible && this.#update();
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
      held.style === at.style &&
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

  /** Which sample the selection's lane shows, and in which style. */
  #sampleAt(): { sample: string | null; columns: unknown; style: number | null } {
    const frame = this.#selection && this.#frames[this.#selection.lane];
    return frame
      ? {
          sample: keyOf(frame.source, frame.dataRevision, frame.sample, ""),
          columns: frame.columns,
          // What the lane keeps of the sample (class toggles, frame filter) changes with the style.
          style: frame.styleRevision,
        }
      : { sample: null, columns: null, style: null };
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
    if (!frame || frame.source !== selection.source) return "gone";
    const summary = summarize(frame, selection.entity);
    if (summary === null) return "gone";
    return summary === "filtered" ? summary : { frame, summary };
  }

  /**
   * Asks for the shown object's fields unless they are held, refused for this very sample, or recently asked for.
   * A request for another sample is superseded, not awaited: the backend keeps only the newest, and a request it
   * dropped is asked again if its sample is shown again.
   */
  #ask(frame: LaneFrame, entity: string): void {
    const key = keyOf(frame.source, frame.dataRevision, frame.sample, entity);
    const refused = this.#refused;
    if (!this.#visible || this.#awaiting || this.#cache.has(key)) return;
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
          (before.state === "absent"
            ? next.state === "absent" &&
              before.reason === next.reason &&
              (before.last === next.last ||
                (before.last !== null && next.last !== null && sameSummary(before.last, next.last)))
            : next.state !== "absent" &&
              sameSummary(before.summary, next.summary) &&
              before.fields === next.fields &&
              before.truncated === next.truncated)));
    if (same) return false;
    this.#view = next;
    return true;
  }

  #build(selection: NonNullable<Selection>): InspectionView {
    const shown = this.#shown(selection);
    if (typeof shown === "string")
      return { state: "absent", selection, reason: shown, last: this.#last };
    const { frame, summary } = shown;
    this.#last = summary;
    const key = keyOf(frame.source, frame.dataRevision, frame.sample, selection.entity);
    this.#wanted = key;
    const reply = this.#cache.get(key);
    if (reply?.state === "Absent")
      return { state: "absent", selection, reason: "gone", last: summary };
    this.#ask(frame, selection.entity);
    // Fields are those of the sample the summary is of, or none: never the preceding sample's.
    const found = reply?.state === "Found" ? reply : null;
    return {
      state: reply ? "ready" : this.#refused?.key === key ? "unavailable" : "loading",
      selection,
      summary,
      fields: found?.fields ?? NO_FIELDS,
      truncated: found?.truncated ?? false,
    };
  }
}

const NO_FIELDS: readonly FieldWire[] = [];
