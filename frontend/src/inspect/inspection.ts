/**
 * What the inspector shows of the selected object. The selection is the viewer's own state and survives seeking and
 * playing by object id. The summary comes at once from the Sample Item the lane shows; every other field comes from
 * `Inspect` for (source, data revision, sample, object), followed by bounded pages and value chunks. Asking for a
 * newer sample supersedes an outstanding request; only the current field window and one explicit copy are kept. A request whose reply never
 * comes (the backend may drop one) is asked again when its sample is wanted again, or after RESEND_AFTER draws.
 * Nothing is asked while no inspector page shows; the selection is kept and asked about when one does. A view only ever holds
 * the shown sample's data: while its reply is out the summary is the shown sample's and there are no fields. While a
 * video plays the view follows the draws at most every PLAYING_INTERVAL_MS; the newest draw replaces those skipped, and
 * `catchUp`, a selection or showing the inspector brings the view up to it at once. A reply completes only the sample
 * the view is of, and never advances it to a draw skipped.
 */

import { MAX_COPY_BYTES } from "../generated/api";
import { writeClipboard } from "../tools/table/clipboard";
import { keyOf, summarize, sameSummary, type Shown } from "./inspectionSummary";
import type { InspectionView, LaneFrame, Selection, Summary } from "../lanes/contract";
import type { ClientMessage, FieldWire, InspectionWire, ServerMessage } from "../stream/protocol";

/** One field window is retained; returning to another sample asks afresh. */
const CACHE_SIZE = 1;

/** The least time between two updates of what the inspector shows while a video plays: 10 Hz, in milliseconds. */
export const PLAYING_INTERVAL_MS = 100;

/** How many draws a request may stay unanswered while its sample is shown before it is asked again. */
const RESEND_AFTER = 120;

type Reply = Extract<ServerMessage, { type: "Inspected" }>;

export class Inspections {
  readonly #changed: () => void;
  #serial = 0;
  #accessRevision = 0;
  #pageOffset = 0;
  #pending: {
    request: number;
    key: string;
    lane: number;
    token: string;
    field: number | null;
    path: boolean;
    offset: number;
  } | null = null;
  #copy: { value: string; received: number; total: number } | null = null;
  #error: string | null = null;
  #copied = false;
  #writing = false;
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
  constructor(
    send: (message: ClientMessage) => void,
    now: () => number = () => performance.now(),
    changed: () => void = () => {},
  ) {
    this.#changed = changed;
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
      selection?.lane !== this.#selection?.lane ||
      selection?.source !== this.#selection?.source ||
      selection?.entity !== this.#selection?.entity
    )
      this.#cancelRead();
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
    if (!visible) {
      this.suspend();
      this.#cancelRead();
    }
    return visible && this.#sync();
  }

  /**
   * The lanes drew these frames: follows the selection to the shown samples. Reports whether the view changed. While
   * `playing` the view follows only if it has not within PLAYING_INTERVAL_MS.
   */
  follow(frames: readonly (LaneFrame | undefined)[], playing = false): boolean {
    const selected = this.#selection;
    const shown = selected && frames[selected.lane];
    const key =
      shown && selected
        ? keyOf(shown.source, shown.dataRevision, shown.sample, selected.entity)
        : null;
    if ((this.#pending || this.#copy || this.#writing) && this.#wanted !== key) this.#cancelRead();
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
    if (key !== this.#wanted) return false;
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
    this.#cancelRead();
    this.#inflight = null;
    this.#refused = null;
    this.#awaiting = true;
    this.#revisions.clear();
    this.#cache.clear();
    this.#update();
  }

  #cancelRead(): void {
    this.#serial++;
    this.#pending = null;
    this.#copy = null;
    this.#error = null;
    this.#copied = false;
    this.#accessRevision++;
  }

  #notify(): void {
    this.#accessRevision++;
    this.#update();
    this.#changed();
  }

  #read(field: number | null, offset: number, path = false): void {
    const key = this.#wanted;
    const reply = key === null ? undefined : this.#cache.get(key);
    if (
      !key ||
      reply?.state !== "Found" ||
      !reply.continuation ||
      !this.#selection ||
      !this.#visible
    )
      return;
    this.#cancelRead();
    if (field !== null && this.#writing) {
      this.#error = "Clipboard is busy";
      this.#notify();
      return;
    }
    if (field !== null) {
      const cut = reply.fields
        .map((entry) => (path ? entry.path_cut : entry.cut))
        .find((cut) => cut?.[0] === field);
      if (!cut) return;
      if (cut[1] > MAX_COPY_BYTES) {
        this.#error = `Value is ${cut[1]} bytes; copying supports at most ${MAX_COPY_BYTES} bytes`;
        this.#notify();
        return;
      }
      this.#copy = { value: "", received: 0, total: cut[1] };
    }
    this.#pending = {
      request: ++this.#serial,
      key,
      lane: this.#selection.lane,
      token: reply.continuation.token,
      field,
      path,
      offset,
    };
    this.#sendRead();
    this.#notify();
  }

  #sendRead(): void {
    const pending = this.#pending;
    if (pending)
      this.#send({
        type: "InspectContinue",
        request: pending.request,
        token: pending.token,
        field: pending.field,
        path: pending.path,
        offset: pending.offset,
      });
  }

  /** Accepts a page or chunk only for the current inspection request, selection, lane and sample. */
  receiveContinuation(message: Extract<ServerMessage, { type: "InspectContinued" }>): boolean {
    const pending = this.#pending;
    if (
      !pending ||
      pending.request !== message.request ||
      pending.key !== this.#wanted ||
      pending.lane !== this.#selection?.lane ||
      !this.#visible
    )
      return false;
    const result = message.result;
    if (result.state === "Refused") {
      this.#cancelRead();
      this.#error = result.reason;
    } else if (result.state === "Page" && pending.field === null) {
      this.#pageOffset = pending.offset;
      this.#remember(pending.key, {
        state: "Found",
        fields: result.fields,
        truncated: result.next !== null,
        continuation: { token: pending.token, next: result.next },
      });
      this.#pending = null;
    } else if (result.state === "Chunk" && this.#copy) {
      const copy = this.#copy;
      copy.value = `${copy.value}${result.text}`;
      copy.received = result.next ?? result.total;
      if (result.next !== null) {
        pending.offset = result.next;
        pending.request = ++this.#serial;
        this.#sendRead();
      } else {
        this.#pending = null;
        this.#commitCopy();
      }
    }
    this.#notify();
    return true;
  }

  #commitCopy(): void {
    const copy = this.#copy;
    if (!copy || copy.received !== copy.total || this.#writing) return;
    const serial = this.#serial;
    this.#copy = null;
    this.#writing = true;
    void writeClipboard(copy.value, () => serial === this.#serial).then((written) => {
      this.#writing = false;
      if (serial !== this.#serial) return;
      this.#copied = written;
      // A fresh click can grant clipboard permission after a long transfer, including an HTTP watcher.
      this.#copy = written ? null : copy;
      this.#error = written ? null : "Could not copy the value";
      this.#notify();
    });
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
    const next = selection ? this.#viewOf(selection) : ({ state: "none" } as const);
    // Asked for or refused, and no longer the shown sample: asked again if it is shown again.
    if (next.state === "none" || next.state === "absent") this.#wanted = null;
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
              before.truncated === next.truncated &&
              before.access?.revision === next.access?.revision)));
    if (same) return false;
    this.#view = next;
    return true;
  }

  #viewOf(selection: NonNullable<Selection>): InspectionView {
    const shown = this.#shown(selection);
    if (typeof shown === "string") {
      this.#cancelRead();
      return { state: "absent", selection, reason: shown, last: this.#last };
    }
    const { frame, summary } = shown;
    this.#last = summary;
    const key = keyOf(frame.source, frame.dataRevision, frame.sample, selection.entity);
    if (this.#wanted !== key) {
      this.#cancelRead();
      this.#cache.clear();
      this.#pageOffset = 0;
    }
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
      access: found?.continuation
        ? {
            revision: this.#accessRevision,
            next:
              found.continuation.next === null || this.#pending
                ? null
                : () => this.#read(null, found.continuation?.next ?? 0),
            first: this.#pageOffset === 0 || this.#pending ? null : () => this.#read(null, 0),
            copy: (ordinal, path) => this.#read(ordinal, 0, path),
            cancel: () => {
              this.#cancelRead();
              this.#notify();
            },
            commit:
              this.#copy?.received === this.#copy?.total && this.#copy !== null
                ? () => {
                    this.#commitCopy();
                    this.#notify();
                  }
                : null,
            progress:
              this.#copy && this.#copy.received < this.#copy.total
                ? { received: this.#copy.received, total: this.#copy.total }
                : null,
            error: this.#error,
            copied: this.#copied,
          }
        : undefined,
    };
  }
}

const NO_FIELDS: readonly FieldWire[] = [];
