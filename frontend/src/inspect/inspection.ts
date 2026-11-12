/**
 * What the inspector shows of the selected object. The selection is the viewer's own state and survives seeking and
 * playing by object id. The summary comes at once from the Sample Item the lane shows; every other field comes from
 * one `Inspect` request per (source, data revision, sample, object): asking for a newer sample supersedes a request
 * still out (the backend keeps only the newest), and the last 16 replies are kept. A request whose reply never
 * comes (the backend may drop one) is asked again when its sample is wanted again, or after RESEND_AFTER draws.
 * Nothing is asked while no inspector page shows; the selection is kept and asked about when one does. A view only ever holds
 * the shown sample's data: while its reply is out the summary is the shown sample's and there are no fields. While a
 * video plays the view follows the draws at most every PLAYING_INTERVAL_MS; the newest draw replaces those skipped, and
 * `catchUp`, a selection or showing the inspector brings the view up to it at once. A reply completes only the sample
 * the view is of, and never advances it to a draw skipped.
 */

import { MOTION_STATES } from "../present/columns";
import { maskKeeps } from "../present/filter/filter";
import type { InspectionView, LaneFrame, Selection, Summary } from "../lanes/contract";
import type { ClientMessage, FieldWire, InspectionWire, ServerMessage } from "../stream/protocol";

/** How many replies are kept, so that stepping back and forth asks the backend once per frame. */
const CACHE_SIZE = 16;

/** The least time between two updates of what the inspector shows while a video plays: 10 Hz, in milliseconds. */
export const PLAYING_INTERVAL_MS = 100;

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
  /** The newest draw; `#frames`, which the view is made from, is it only once the view has followed it. */
  #newest: readonly (LaneFrame | undefined)[] = [];
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
  readonly #now: () => number;
  /** When the view last followed a draw, and whether a draw has been skipped since. */
  #followedAt = Number.NEGATIVE_INFINITY;
  #behind = false;
  #cancel: (() => void) | undefined;

  /** `send` asks the backend for an object's fields; `now` reads the time in milliseconds. */
  constructor(send: (message: ClientMessage) => void, now: () => number = () => performance.now()) {
    this.#send = send;
    this.#now = now;
  }

  /**
   * After a draw: if the view skipped it, brings the view up to date with `changed` once PLAYING_INTERVAL_MS pass
   * without another draw, as when the source stalls. Each draw restarts the wait.
   */
  trail(after: (callback: () => void, ms: number) => () => void, changed: () => void): void {
    this.#cancel?.();
    this.#cancel =
      this.#behind && this.#visible
        ? after(() => {
            if (this.catchUp()) changed();
          }, PLAYING_INTERVAL_MS)
        : undefined;
  }

  /** Brings the view up to the newest draw after skipped ones. Reports whether the view changed. */
  catchUp(): boolean {
    this.#cancel?.();
    this.#cancel = undefined;
    if (!this.#behind) return false;
    this.#followedAt = this.#now();
    return this.#sync();
  }

  /** Stops the wait to catch up, for an inspector that is hidden or closed; showing one catches up. */
  suspend(): void {
    this.#cancel?.();
    this.#cancel = undefined;
  }

  /** Makes the view follow the newest draw. Reports whether the view changed. */
  #sync(): boolean {
    this.#frames = this.#newest;
    this.#behind = false;
    return this.#update();
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
    return this.#sync();
  }

  /** Tells whether an inspector page shows. Showing one asks for the selection's fields at once. Reports whether the view changed. */
  setVisible(visible: boolean): boolean {
    if (visible === this.#visible) return false;
    this.#visible = visible;
    if (!visible) this.suspend();
    return visible && this.#sync();
  }

  /**
   * The lanes drew these frames: follows the selection to the shown samples. Reports whether the view changed. While
   * `playing` the view follows only if it has not within PLAYING_INTERVAL_MS.
   */
  follow(frames: readonly (LaneFrame | undefined)[], playing = false): boolean {
    this.#newest = frames;
    const resumed = this.#awaiting;
    this.#awaiting = false;
    this.#draws++;
    for (const frame of frames) if (frame) this.#revisions.set(frame.source, frame.dataRevision);
    const now = this.#now();
    if (playing && !resumed && now - this.#followedAt < PLAYING_INTERVAL_MS) {
      this.#behind = true;
      return false;
    }
    this.#frames = frames;
    this.#behind = false;
    this.#followedAt = now;
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
    const next = selection ? this.#viewOf(selection) : ({ state: "none" } as const);
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

  #viewOf(selection: NonNullable<Selection>): InspectionView {
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
