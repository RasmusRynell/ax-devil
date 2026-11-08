/**
 * The events a lane's whole overlay file holds, as the client asks for them: a page of the lane's Events list, and one
 * event with its objects. Like File scope's objects, a viewer says what it wants per lane (`request`, `want`) and reads
 * the latest answer (`view`, `detail`); this keeps the backend asked for exactly that, again when the answer would
 * change (a new revision, the file's ingest ending, the viewer owning its frames again), and drops answers of an older
 * request or of other revisions. The list and the event are asked and answered apart, so a page that opens over the
 * list does not disturb it.
 */

import type { EventDetailWire, EventRowWire, FileScopeUnavailable } from "../generated/api";
import { parseSearch } from "../search/query";
import { basisOf, FOLLOWING_RETRIES, FOLLOWING_RETRY_MS, readyOf } from "./fileBasis";
import type { LaneSet } from "./lanes";
import type { ClientMessage, ServerMessage } from "./protocol";

/** How long a change to the wanted list waits for another before it is sent. */
const DEBOUNCE_MS = 150;
/** The most often a list is asked again only because the frame on screen moved. */
const FRAME_MS = 250;

/** One page of a lane's Events list. */
export type EventsPage = {
  /** The data revision it was listed under. */
  revision: number;
  /** How many events the video shows. */
  total: number;
  /** How many of them the search matches, which is how many rows the list has. */
  matched: number;
  /** The position in the list of the first of `rows`. */
  offset: number;
  rows: readonly EventRowWire[];
  /** Where the event asked about is in the whole list, whichever page this is; `null` when none was or it is not there. */
  located: { event: number; row: number } | null;
  /** The frame the page was asked about, if any. */
  frame: number | null;
  /** How many rows are on frames at or before `frame`: where the line for it goes. */
  frameRow: number | null;
};

/**
 * What a lane's Events list shows now. Replaced, never mutated, when it changes. The page is out of date when both it
 * and `unavailable` are set: a refresh failed and the rows are those of the last answer.
 */
export type EventsView = {
  /** An answer is awaited, or the question is about to be asked again. */
  pending: boolean;
  page: EventsPage | null;
  /** Why the backend has no rows; `null` otherwise. */
  unavailable: FileScopeUnavailable | null;
};

/** One event of a lane, as the backend answered it; `detail` is `null` when the file has no such event. */
export type EventView = {
  event: number;
  /** The data revision it was read under. */
  revision: number;
  detail: EventDetailWire | null;
};

const IDLE: EventsView = { pending: false, page: null, unavailable: null };

/** What a lane's list is wanted to be. */
type Wanted = {
  text: string;
  offset: number;
  limit: number;
  locate: number | null;
  frame: number | null;
};

/** What the request of a list or an event waits for. */
type Pending = {
  /** The request the answer awaited belongs to; `null` when none is awaited. */
  asked: number | null;
  /** What the last request was made under, as JSON; `null` when none stands. */
  basis: string | null;
  retries: number;
  cancel: (() => void) | null;
};

type ListSlot = Pending & {
  wanted: Wanted;
  /** What the request awaited was made of. */
  asking: Wanted | null;
  view: EventsView;
  pageData: number | null;
};
type DetailSlot = Pending & {
  event: number;
  bins: number;
  view: EventView | null;
  unavailable: FileScopeUnavailable | null;
};

const fresh = (): Pending => ({ asked: null, basis: null, retries: 0, cancel: null });

export class FileEvents {
  readonly #lanes: LaneSet;
  readonly #send: (message: ClientMessage) => void;
  readonly #after: (callback: () => void, ms: number) => () => void;
  readonly #owner: () => boolean;
  readonly #changed: () => void;
  readonly #released: (lane: number) => void;
  readonly #lists = new Map<number, ListSlot>();
  readonly #details = new Map<number, DetailSlot>();
  #requests = 0;

  /**
   * `owner` says whether the viewer chooses its own frames; `changed` is called when a view changes; `released` when a
   * lane has neither a list nor an event wanted any more, so that what the backend kept for it can be freed.
   */
  constructor(
    lanes: LaneSet,
    send: (message: ClientMessage) => void,
    after: (callback: () => void, ms: number) => () => void,
    owner: () => boolean,
    changed: () => void,
    released: (lane: number) => void,
  ) {
    this.#lanes = lanes;
    this.#send = send;
    this.#after = after;
    this.#owner = owner;
    this.#changed = changed;
    this.#released = released;
  }

  /** What the lane's Events list shows now; the same object until it changes. */
  view(lane: number): EventsView {
    return this.#lists.get(lane)?.view ?? IDLE;
  }

  /** The event the lane wants, once the backend has answered; `null` until then. */
  event(lane: number): EventView | null {
    return this.#details.get(lane)?.view ?? null;
  }

  /** Why the lane's event is not known, once the backend has said so; `null` otherwise. */
  eventUnavailable(lane: number): FileScopeUnavailable | null {
    return this.#details.get(lane)?.unavailable ?? null;
  }

  /** Whether a list or an event is wanted on the lane. */
  wants(lane: number): boolean {
    return this.#lists.has(lane) || this.#details.has(lane);
  }

  /**
   * Wants the lane's list to be `limit` rows from `offset` of the events `text` searches. `locate` names an event whose
   * row in the whole list the page tells too; `frame` a frame whose place in it does. Asking for what is already wanted
   * does nothing; a change of `frame` alone is sent within `FRAME_MS`, however often it changes, the others once they
   * have paused for `DEBOUNCE_MS`.
   */
  request(
    lane: number,
    text: string,
    offset: number,
    limit: number,
    locate: number | null,
    frame: number | null,
  ): void {
    const wanted: Wanted = { text, offset, limit, locate, frame };
    const held = this.#lists.get(lane);
    if (held) {
      const { frame: before, ...rest } = held.wanted;
      const { frame: now, ...same } = wanted;
      if (JSON.stringify(rest) === JSON.stringify(same)) {
        held.wanted = wanted;
        // A frame the page was not asked about is sent after a pause; one already pending is not pushed back, and none
        // is sent while an answer is awaited, which a newer request would make stale: the answer sends it.
        if (now !== before && now !== null && held.cancel === null && held.asked === null)
          this.#schedule(lane, held, FRAME_MS);
        return;
      }
      Object.assign(held, { wanted, asked: null, retries: 0 });
      this.#schedule(lane, held, DEBOUNCE_MS);
      this.#replaceList(lane, held, { ...held.view, pending: true });
      return;
    }
    const slot: ListSlot = { ...fresh(), wanted, asking: null, view: IDLE, pageData: null };
    this.#lists.set(lane, slot);
    this.#schedule(lane, slot, DEBOUNCE_MS);
    this.#replaceList(lane, slot, { ...slot.view, pending: true });
  }

  /** Asks for the wanted list now, instead of when the pause for more changes ends. */
  flush(lane: number): void {
    const slot = this.#lists.get(lane);
    if (!slot) return;
    slot.cancel?.();
    slot.cancel = null;
    slot.retries = 0;
    this.#ask(lane, slot);
    this.#replaceList(lane, slot, { ...slot.view, pending: true });
  }

  /** Stops wanting the lane's list, as when the panel is gone. */
  release(lane: number): void {
    const slot = this.#lists.get(lane);
    if (!slot) return;
    slot.cancel?.();
    this.#lists.delete(lane);
    this.#changed();
    this.#freed(lane, slot.basis !== null);
  }

  /** Wants `event` of the lane, with presence in at most `bins` spans per object, or nothing with `null`. */
  want(lane: number, event: number | null, bins = 0): void {
    const held = this.#details.get(lane);
    if (held?.event === event && held.bins === bins) return;
    held?.cancel?.();
    if (event === null) {
      this.#details.delete(lane);
      if (held?.view) this.#changed();
      if (held) this.#freed(lane, held.basis !== null);
      return;
    }
    const slot: DetailSlot = { ...fresh(), event, bins, view: null, unavailable: null };
    this.#details.set(lane, slot);
    this.#askEvent(lane, slot);
    if (held?.view) this.#changed();
  }

  /** The lanes changed: ask again for each list and event whose answer would differ. */
  refresh(): void {
    for (const [lane, slot] of this.#lists) {
      if (slot.cancel === null && this.#key(lane) !== slot.basis) {
        slot.retries = 0;
        slot.asked = null;
        this.#schedule(lane, slot, DEBOUNCE_MS);
        this.#replaceList(lane, slot, { ...slot.view, pending: true });
      }
    }
    for (const [lane, slot] of this.#details) {
      if (slot.cancel !== null || this.#key(lane) === slot.basis) continue;
      slot.retries = 0;
      this.#askEvent(lane, slot);
    }
  }

  /** The socket is gone: forget what was awaited. The lists and events wanted stay wanted. */
  suspend(): void {
    for (const slot of [...this.#lists.values(), ...this.#details.values()]) {
      slot.cancel?.();
      Object.assign(slot, { cancel: null, asked: null, basis: null });
    }
  }

  /** Takes the backend's answer to a request, unless a newer request or other revisions have made it stale. */
  receive(message: Extract<ServerMessage, { type: "EventRows" | "Event" }>): void {
    const lane = message.lane;
    const state = this.#lanes.states[lane];
    const current = (data: number, policy: number) =>
      readyOf(state)?.data_revision === data && state.policyRevision === policy;
    if (message.type === "Event") {
      const slot = this.#details.get(lane);
      if (!slot || slot.asked !== message.request || slot.event !== message.event) return;
      const { detail } = message;
      if (detail.state === "Found") {
        if (!current(detail.data_revision, detail.policy_revision)) return;
        slot.asked = null;
        slot.unavailable = null;
        this.#setEvent(slot, {
          event: slot.event,
          revision: detail.data_revision,
          detail: detail.detail,
        });
        return;
      }
      this.#failed(lane, slot, detail.unavailable, () => {
        slot.cancel = this.#after(() => {
          slot.cancel = null;
          if (this.#details.get(lane) === slot) this.#askEvent(lane, slot);
        }, FOLLOWING_RETRY_MS);
      });
      // The view may already be null, so the reason is published on its own.
      slot.unavailable = detail.unavailable;
      slot.view = null;
      this.#changed();
      return;
    }
    const slot = this.#lists.get(lane);
    if (!slot || slot.asked !== message.request) return;
    const { rows } = message;
    if (rows.state === "Found") {
      if (!current(rows.data_revision, rows.policy_revision)) return;
      slot.asked = null;
      slot.pageData = rows.data_revision;
      this.#replaceList(lane, slot, {
        pending: false,
        page: {
          revision: rows.data_revision,
          total: rows.total,
          matched: rows.matched,
          offset: rows.offset,
          rows: rows.rows,
          located:
            rows.located !== null && slot.asking?.locate != null
              ? { event: slot.asking.locate, row: rows.located }
              : null,
          frame: slot.asking?.frame ?? null,
          frameRow: rows.frame_row,
        },
        unavailable: null,
      });
      // The frame moved on while the question was out.
      const frame = slot.wanted.frame;
      if (frame !== null && frame !== slot.asking?.frame && slot.cancel === null)
        this.#schedule(lane, slot, FRAME_MS);
      return;
    }
    const retry = this.#failed(lane, slot, rows.unavailable, () =>
      this.#schedule(lane, slot, FOLLOWING_RETRY_MS),
    );
    // A page that could not be refreshed stays, flagged by `unavailable`, as long as the file's data is what it showed.
    const keep =
      rows.unavailable.state === "Failed" && slot.pageData === readyOf(state)?.data_revision;
    this.#replaceList(lane, slot, {
      pending: retry,
      page: keep ? slot.view.page : null,
      unavailable: rows.unavailable,
    });
  }

  /** Ends the wait for an answer that was none; asks again by `again` while a follower's authority may still arrive. */
  #failed(lane: number, slot: Pending, why: FileScopeUnavailable, again: () => void): boolean {
    slot.asked = null;
    // The backend may not yet know the viewer owns its frames again; nothing tells this client when it does.
    const retry = why.state === "Following" && this.#owner() && slot.retries < FOLLOWING_RETRIES;
    if (retry) {
      slot.retries++;
      again();
    }
    return retry;
  }

  /** Tells the backend to free the lane when nothing of it is wanted, if it was asked at all. */
  /** What the lane's event list membership depends on (matching basis, data and policy revision); a list asked under another differs. */
  basis(lane: number): string {
    return this.#key(lane);
  }

  #freed(lane: number, asked: boolean): void {
    if (asked && !this.wants(lane)) this.#released(lane);
  }

  #key(lane: number): string {
    return JSON.stringify(basisOf(this.#lanes, lane, this.#owner()));
  }

  #schedule(lane: number, slot: ListSlot, ms: number): void {
    slot.cancel?.();
    slot.cancel = this.#after(() => {
      slot.cancel = null;
      this.#ask(lane, slot);
    }, ms);
  }

  #ask(lane: number, slot: ListSlot): void {
    if (this.#lists.get(lane) !== slot) return;
    slot.basis = this.#key(lane);
    slot.asked = ++this.#requests;
    slot.asking = slot.wanted;
    const { text, offset, limit, locate, frame } = slot.wanted;
    this.#send({
      type: "EventQuery",
      lane,
      request: slot.asked,
      query: parseSearch(text).wire,
      offset,
      limit,
      locate,
      frame,
    });
  }

  #askEvent(lane: number, slot: DetailSlot): void {
    slot.basis = this.#key(lane);
    slot.asked = ++this.#requests;
    this.#send({ type: "Event", lane, request: slot.asked, event: slot.event, bins: slot.bins });
    this.#setEvent(slot, null);
  }

  #replaceList(lane: number, slot: ListSlot, view: EventsView): void {
    if (this.#lists.get(lane) !== slot) return;
    slot.view = view;
    this.#changed();
  }

  #setEvent(slot: DetailSlot, view: EventView | null): void {
    if (slot.view === view) return;
    slot.view = view;
    this.#changed();
  }
}
