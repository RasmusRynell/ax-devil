/** Inspection state and ordering for a viewer. */

import { MAX_COPY_BYTES, MAX_INSPECTION_PINS } from "../generated/api";
import { InspectionPins } from "./inspectionPins";
import type { FieldId } from "./fields";
import { writeClipboard } from "../tools/table/clipboard";
import { keyOf, summarize, sameInspection, type Shown } from "./inspectionSummary";
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
  readonly #pins = new InspectionPins();
  #accessRevision = 0;
  #pageOffset = 0;
  #pending: {
    request: number;
    key: string;
    lane: number;
    token: string;
    field: number | "raw" | "pins" | null;
    path: boolean;
    offset: number;
  } | null = null;
  #copy: { value: string; received: number; total: number } | null = null;
  #error: string | null = null;
  #copied = false;
  #capActive = false;
  #writing = false;
  #writingCopy: { value: string } | null = null;
  readonly #send: (message: ClientMessage) => void;
  #selection: Selection = null;
  #frames: readonly (LaneFrame | undefined)[] = [];
  /** The newest draw; `#frames`, which the view is made from, is it only once the view has followed it. */
  #newest: readonly (LaneFrame | undefined)[] = [];
  /** The newest data revision each source was drawn with: a reply of an older one is dropped. */
  readonly #revisions = new Map<number, number>();
  /** The replies kept, oldest first. */
  readonly #cache = new Map<string, InspectionWire>();
  /** The request out for the shown sample, and the draw it was sent at. */
  #wanted: string | null = null;
  #inflight: { key: string; at: number; request: number } | null = null;
  /** The sample, as held by these columns, the backend said it would not answer for. */
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

  /** After a draw: if the view skipped it, brings the view up to date with `changed` once PLAYING_INTERVAL_MS pass without another draw, as when the source stalls. */
  trail(after: (callback: () => void, ms: number) => () => void, changed: () => void): void {
    this.#cancel?.();
    this.#cancel =
      this.#behind && this.#visible
        ? after(() => {
            if (this.catchUp()) changed();
          }, PLAYING_INTERVAL_MS)
        : undefined;
  }

  /** Brings the view up to the newest draw after skipped ones. */
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

  /** Ends connection ownership: pending chunks and clipboard retries become stale; submitted native writes cannot be revoked. */
  disconnect(): void {
    this.suspend();
    this.reconnect();
    this.#frames = [];
    this.#newest = [];
    this.#last = null;
    this.#wanted = null;
    this.#view = { state: "none" };
    this.#at = null;
  }

  /** Makes the view follow the newest draw. */
  #sync(): boolean {
    this.#frames = this.#newest;
    this.#behind = false;
    return this.#update();
  }

  /** What the inspector shows; the same object until something in it changed. */
  get view(): InspectionView {
    return this.#view;
  }

  /** Selects an object, or clears the selection with `null`. */
  select(selection: Selection): boolean {
    if (this.#error) {
      this.#abandon();
    }
    if (
      selection?.lane !== this.#selection?.lane ||
      selection?.source !== this.#selection?.source ||
      selection?.entity !== this.#selection?.entity
    )
      this.#abandon();
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

  /** Tells whether an inspector page shows. */
  setVisible(visible: boolean): boolean {
    if (visible === this.#visible) return false;
    this.#visible = visible;
    if (!visible) {
      this.suspend();
      this.#abandon();
      return this.#update();
    }
    return this.#sync();
  }

  /** The lanes drew these frames: follows the selection to the shown samples. */
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

  /** The backend's answer to a request for fields. */
  receive(message: Reply): boolean {
    const key = keyOf(message.source, message.data_revision, message.sample, message.entity);
    if (
      message.request !== this.#inflight?.request ||
      key !== this.#wanted ||
      !this.#capActive ||
      !this.#visible
    )
      return false;
    if (key === this.#inflight?.key) this.#inflight = null;
    if (message.data_revision === this.#revisions.get(message.source)) {
      if (message.inspection.state === "Unavailable") {
        this.#abandon();
        this.#refused = { key, columns: this.#sampleAt().columns };
      } else {
        if (message.inspection.state === "Refused") this.#abandon();
        this.#remember(key, message.inspection);
      }
    }
    const changed = this.#update();
    this.#askPins();
    return changed;
  }

  /** A new connection: nothing of the old one holds. */
  reconnect(): void {
    this.#abandon();
    this.#inflight = null;
    this.#refused = null;
    this.#awaiting = true;
    this.#revisions.clear();
    this.#cache.clear();
    this.#update();
  }

  /** Resolves pinned identities against the current sample independently of paging. */
  setPins(ids: readonly FieldId[]): boolean {
    if (!this.#pins.set(ids)) return false;
    this.#cancelRead();
    this.#askPins();
    if (ids.length > MAX_INSPECTION_PINS)
      this.#error = `Inspection supports at most ${MAX_INSPECTION_PINS} pins`;
    return this.#update();
  }

  #askPins(): void {
    if (this.#pins.next !== null && !this.#pending && !this.#copy && !this.#writing)
      this.#read("pins", this.#pins.next);
  }

  #abandon(): void {
    if (this.#capActive) this.#send({ type: "InspectRelease" });
    this.#capActive = false;
    this.#cancelRead();
    this.#cache.clear();
    this.#pins.clear();
    this.#inflight = null;
    this.#refused = null;
  }

  #cancelRead(): void {
    if (this.#pending?.field === "pins") this.#pins.restart();
    this.#serial++;
    if (this.#writingCopy) this.#writingCopy.value = "";
    this.#writingCopy = null;
    this.#writing = false;
    this.#pending = null;
    this.#copy = null;
    this.#error = null;
    this.#copied = false;
    this.#accessRevision++;
  }

  #notify(): void {
    this.#askPins();
    this.#accessRevision++;
    this.#update();
    this.#changed();
  }

  #read(field: number | "raw" | "pins" | null, offset: number, path = false): void {
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
    if (field !== "pins") this.#cancelRead();
    if (field !== null && this.#writing) {
      this.#error = "Clipboard is busy";
      this.#notify();
      return;
    }
    if (field !== null && field !== "raw" && field !== "pins") {
      const cut = [
        ...reply.fields,
        ...this.#pins.fields.filter(
          (field): field is FieldWire => field != null && typeof field !== "string",
        ),
      ]
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
    if (field === "raw") this.#copy = { value: "", received: 0, total: MAX_COPY_BYTES };
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
    if (
      pending?.field === "pins" &&
      new TextEncoder().encode(JSON.stringify(this.#pins.wanted())).length > 60 * 1024
    ) {
      this.#pins.refuse();
      this.#pending = null;
      this.#cancelRead();
      this.#error = "Pinned fields exceed the inspection request budget";
      this.#notify();
      return;
    }
    if (pending)
      this.#send({
        type: "InspectContinue",
        request: pending.request,
        token: pending.token,
        field: typeof pending.field === "number" ? pending.field : null,
        pins: pending.field === "pins" ? this.#pins.wanted() : undefined,
        raw: pending.field === "raw" ? true : undefined,
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
      if (pending.field === "pins") {
        this.#pins.refuse();
        this.#pending = null;
      }
      this.#cancelRead();
      this.#error = result.reason;
    } else if (result.state === "Pins" && pending.field === "pins") {
      this.#pins.accept(result.fields, result.next);
      this.#pending = null;
      this.#askPins();
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
      copy.total = result.total;
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
    this.#writingCopy = copy;
    void writeClipboard(
      () => copy.value,
      () => serial === this.#serial,
    ).then((written) => {
      if (serial !== this.#serial) return;
      this.#writing = false;
      this.#writingCopy = null;
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

  /** Asks for the shown object's fields unless they are held, refused for this very sample, or recently asked for. */
  #ask(frame: LaneFrame, entity: string): void {
    const key = keyOf(frame.source, frame.dataRevision, frame.sample, entity);
    const refused = this.#refused;
    if (!this.#visible || this.#awaiting || this.#cache.has(key)) return;
    if (refused?.key === key && refused.columns === frame.columns) return;
    if (this.#inflight?.key === key && !this.#overdue()) return;
    this.#capActive = true;
    this.#inflight = { key, at: this.#draws, request: ++this.#serial };
    this.#send({
      type: "Inspect",
      request: this.#inflight.request,
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
    if (sameInspection(before, next)) return false;
    this.#view = next;
    return true;
  }

  #viewOf(selection: NonNullable<Selection>): InspectionView {
    const shown = this.#shown(selection);
    if (typeof shown === "string") {
      this.#abandon();
      return { state: "absent", selection, reason: shown, last: this.#last };
    }
    const { frame, summary } = shown;
    this.#last = summary;
    const key = keyOf(frame.source, frame.dataRevision, frame.sample, selection.entity);
    if (this.#wanted !== key) {
      this.#abandon();
      this.#pageOffset = 0;
    }
    this.#wanted = key;
    const reply = this.#cache.get(key);
    if (reply?.state === "Absent") {
      this.#abandon();
      return { state: "absent", selection, reason: "gone", last: summary };
    }
    this.#ask(frame, selection.entity);
    const found = reply?.state === "Found" ? reply : null;
    return {
      state: reply ? "ready" : this.#refused?.key === key ? "unavailable" : "loading",
      selection,
      summary,
      error: reply?.state === "Refused" ? reply.reason : undefined,
      fields: found?.fields ?? NO_FIELDS,
      pins: this.#pins.fields,
      truncated: found?.truncated ?? false,
      access: found?.continuation
        ? {
            revision: this.#accessRevision,
            next:
              found.continuation.next === null || this.#pending
                ? null
                : () => this.#read(null, found.continuation?.next ?? 0),
            first: this.#pageOffset === 0 || this.#pending ? null : () => this.#read(null, 0),
            raw: () => this.#read("raw", 0),
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
