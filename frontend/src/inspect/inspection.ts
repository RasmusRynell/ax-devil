import { readMessage, validChunk, type InspectionRead } from "./inspectionRead";
import { utf8Bytes } from "./utf8";
import {
  afterInspection,
  INSPECTION_RETRIES,
  INSPECTION_RETRY_MS,
  INSPECTION_WAIT_MS,
} from "./requestTiming";
/** Inspection state and ordering for a viewer. */

import {
  MAX_COPY_BYTES,
  MAX_INSPECTION_PINS,
  MAX_INSPECTION_ENTITY_BYTES,
  MAX_INSPECTION_CHUNK_BYTES,
} from "../generated/api";
import { InspectionPins } from "./inspectionPins";
import type { FieldId } from "./fields";
import { writeClipboard } from "../tools/table/clipboard";
import { keyOf, sampleAt, shownAt, sameInspection } from "./inspectionSummary";
import type { InspectionView, LaneFrame, Selection, Summary } from "../lanes/contract";
import type { ClientMessage, FieldWire, InspectionWire, ServerMessage } from "../stream/protocol";

/** One field window is retained; returning to another sample asks afresh. */
const CACHE_SIZE = 1;

/** The least time between two updates of what the inspector shows while a video plays: 10 Hz, in milliseconds. */
export const PLAYING_INTERVAL_MS = 100;

type Reply = Extract<ServerMessage, { type: "Inspected" }>;

export class Inspections {
  readonly #changed: () => void;
  #serial = 0;
  readonly #after: typeof afterInspection;
  #cancelInitial: (() => void) | null = null;
  #cancelWait: (() => void) | null = null;
  readonly #pins = new InspectionPins();
  #accessRevision = 0;
  #pageOffset = 0;
  #pending: InspectionRead | null = null;
  #copy: { value: string; received: number; total: number } | null = null;
  #error: string | null = null;
  #copied = false;
  #cap: Extract<ClientMessage, { type: "InspectRelease" }> | null = null;
  #writing = false;
  #writingCopy: { value: string } | null = null;
  readonly #send: (message: ClientMessage) => void;
  #selection: Selection = null;
  #frames: readonly (LaneFrame | undefined)[] = [];
  /** The newest draw; `#frames`, which the view is made from, is it only once the view has followed it. */
  #newest: readonly (LaneFrame | undefined)[] = [];
  readonly #revisions = new Map<number, number>();
  readonly #cache = new Map<string, InspectionWire>();
  #wanted: string | null = null;
  #initial: {
    key: string;
    columns: unknown;
    request: number;
    retries: number;
    state: "pending" | "unavailable";
  } | null = null;
  #visible = true;
  /** Waits for the new connection's first draw. */
  #awaiting = false;
  /** The summary retained while the object is absent. */
  #last: Summary | null = null;
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
    after: typeof afterInspection = afterInspection,
  ) {
    this.#after = after;
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
    if (this.#error || this.#view.state === "unavailable") {
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
    for (const frame of frames) if (frame) this.#revisions.set(frame.source, frame.dataRevision);
    const now = this.#now();
    if (playing && !resumed && now - this.#followedAt < PLAYING_INTERVAL_MS) {
      this.#behind = true;
      return false;
    }
    this.#frames = frames;
    this.#behind = false;
    this.#followedAt = now;
    const at = sampleAt(this.#selection, this.#frames);
    const held = this.#at;
    if (
      held?.selection === this.#selection &&
      held.sample === at.sample &&
      held.columns === at.columns &&
      held.style === at.style &&
      !resumed
    )
      return false;
    return this.#update();
  }

  /** The backend's answer to a request for fields. */
  receive(message: Reply): boolean {
    const key = keyOf(message.source, message.data_revision, message.sample, message.entity);
    if (
      this.#initial?.state !== "pending" ||
      message.request !== this.#initial.request ||
      message.data_revision !== this.#revisions.get(message.source) ||
      key !== this.#wanted ||
      !this.#cap ||
      !this.#visible
    )
      return false;
    if (message.inspection.state === "Unavailable") {
      this.#refuseInitial();
    } else {
      this.#initial = null;
      this.#cancelInitial?.();
      this.#cancelInitial = null;
      if (message.inspection.state === "Found" && message.inspection.continuation)
        this.#cap = { type: "InspectRelease", token: message.inspection.continuation.token };
      if (message.inspection.state === "Refused") this.#abandon();
      this.#remember(key, message.inspection);
    }
    const changed = this.#update();
    this.#askPins();
    return changed;
  }

  /** A new connection: nothing of the old one holds. */
  reconnect(): void {
    this.#abandon();
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

  #refuseInitial(): void {
    const request = this.#initial;
    this.#abandon();
    if (request) {
      request.state = "unavailable";
      this.#initial = request;
    }
  }

  #abandon(): void {
    this.#cancelInitial?.();
    this.#cancelInitial = null;
    if (this.#cap) this.#send(this.#cap);
    this.#cap = null;
    this.#cancelRead();
    this.#cache.clear();
    this.#pins.clear();
    this.#initial = null;
  }

  #cancelRead(): void {
    this.#cancelWait?.();
    this.#cancelWait = null;
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
    if (!pending) return;
    const message = readMessage(pending, pending.field === "pins" ? this.#pins.wanted() : []);
    if (typeof message === "string") {
      this.#failRead(message);
      this.#notify();
      return;
    }
    this.#cancelWait?.();
    const request = pending.request;
    this.#cancelWait = this.#after(() => {
      if (this.#pending?.request !== request) return;
      this.#failRead("Details are not available");
      this.#notify();
    }, INSPECTION_WAIT_MS);
    this.#send(message);
  }

  #failRead(reason: string): void {
    if (this.#pending?.field === "pins") this.#pins.refuse();
    this.#pending = null;
    this.#cancelRead();
    this.#error = reason;
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
    this.#cancelWait?.();
    this.#cancelWait = null;
    const result = message.result;
    if (result.state === "Refused") {
      this.#failRead(result.reason);
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
      const bytes = utf8Bytes(result.text, MAX_INSPECTION_CHUNK_BYTES);
      const received = copy.received + bytes;
      if (!validChunk(copy, result, bytes, pending.field === "raw")) {
        this.#failRead("Details are not available");
      } else {
        copy.total = result.total;
        copy.value = `${copy.value}${result.text}`;
        copy.received = received;
        if (result.next !== null) {
          pending.offset = result.next;
          pending.request = ++this.#serial;
          this.#sendRead();
        } else {
          this.#pending = null;
          this.#commitCopy();
        }
      }
    } else {
      this.#failRead("Details are not available");
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

  #remember(key: string, inspection: InspectionWire): void {
    this.#cache.delete(key);
    this.#cache.set(key, inspection);
    if (this.#cache.size > CACHE_SIZE)
      this.#cache.delete(this.#cache.keys().next().value as string);
  }

  /** Only the response timer retries an existing initial request. */
  #ask(frame: LaneFrame, entity: string, retry = false): void {
    const key = keyOf(frame.source, frame.dataRevision, frame.sample, entity);
    if (!this.#visible || this.#awaiting || this.#cache.has(key)) return;
    if (this.#initial && (this.#initial.key !== key || this.#initial.columns !== frame.columns))
      this.#abandon();
    if (this.#initial) {
      if (this.#initial.state === "unavailable" || !retry) return;
      if (this.#initial.retries >= INSPECTION_RETRIES) {
        this.#refuseInitial();
        return;
      }
      this.#initial.retries++;
    }
    this.#initial ??= {
      key,
      columns: frame.columns,
      request: ++this.#serial,
      retries: 0,
      state: "pending",
    };
    if (utf8Bytes(entity, MAX_INSPECTION_ENTITY_BYTES) > MAX_INSPECTION_ENTITY_BYTES) {
      this.#refuseInitial();
      return;
    }
    this.#cap = { type: "InspectRelease", request: this.#initial.request };
    const request = this.#initial.request;
    this.#cancelInitial?.();
    this.#cancelInitial = this.#after(() => {
      if (this.#initial?.request !== request) return;
      this.#ask(frame, entity, true);
      this.#update();
      this.#changed();
    }, INSPECTION_RETRY_MS);
    this.#send({
      type: "Inspect",
      request: this.#initial.request,
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
    this.#at = { selection, ...sampleAt(this.#selection, this.#frames) };
    const before = this.#view;
    if (sameInspection(before, next)) return false;
    this.#view = next;
    return true;
  }

  #viewOf(selection: NonNullable<Selection>): InspectionView {
    const shown = shownAt(selection, this.#frames);
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
      state: reply ? "ready" : this.#initial?.state === "unavailable" ? "unavailable" : "loading",
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
