/**
 * File scope as the client asks for it: the objects a lane's whole overlay file holds, listed by the backend. A viewer
 * says what list it wants per lane (`request`) and reads the latest answer (`view`); this keeps the backend asked for
 * exactly that list. It asks again when the answer would change: a new Ready revision, a new policy revision, the
 * class toggles, the file's ingest ending and the viewer becoming the owner of its frames again, for the backend
 * announces none of these. Answers of an older request, or made under other revisions, are dropped. Releasing a lane
 * tells the backend to free what it kept for the lane.
 */

import type { FileRowWire, FileScopeUnavailable, FileSortWire } from "../generated/api";
import { parseSearch } from "../search/query";
import type { LaneState } from "./contract";
import type { LaneSet, ReadyStatus } from "./lanes";
import { styleOf } from "./laneStyle";
import type { ClientMessage, ServerMessage } from "./protocol";

/** How long a change to the wanted list waits for another before it is sent. */
const DEBOUNCE_MS = 150;
/** How long to wait before asking again after a follower's answer to a viewer that believes it owns its frames. */
const FOLLOWING_RETRY_MS = 1000;
/** How often that is tried: the backend learns of a viewer's authority a moment after the viewer does. */
const FOLLOWING_RETRIES = 3;

/** One page of a lane's list. */
type FilePage = {
  /** How many objects the video shows that the class toggles keep. */
  total: number;
  /** How many of them the search matches. */
  matched: number;
  /** The position in the list of the first of `rows`. */
  offset: number;
  rows: readonly FileRowWire[];
};

/** What a lane's list shows now. Replaced, never mutated, when it changes. */
type FileScopeView = {
  /** An answer is awaited, or the question is about to be asked again. */
  pending: boolean;
  /** The last page accepted; kept while a newer one is awaited, `null` when the backend has no rows to give. */
  page: FilePage | null;
  /** Why the backend has no rows; `null` otherwise. */
  unavailable: FileScopeUnavailable | null;
};

const IDLE: FileScopeView = { pending: false, page: null, unavailable: null };

/** What a lane's list is wanted to be. */
type Wanted = {
  query: ReturnType<typeof parseSearch>["wire"];
  sort: FileSortWire;
  offset: number;
  limit: number;
  bins: number;
};

const readyOf = (state: LaneState | undefined): ReadyStatus | undefined =>
  state && state.status !== "plain" && state.status.status === "Ready" ? state.status : undefined;

/** What the backend's answer depends on: it is asked again when this changes. */
type Basis = { data: number; policy: number; reading: boolean; owner: boolean; classes: number[] };

type Slot = {
  wanted: Wanted;
  view: FileScopeView;
  /** The request the answer awaited belongs to; `null` when none is awaited. */
  asked: number | null;
  /** What the last request was made under, as JSON; `null` when none stands. */
  basis: string | null;
  retries: number;
  cancel: (() => void) | null;
};

export class FileScope {
  readonly #lanes: LaneSet;
  readonly #send: (message: ClientMessage) => void;
  readonly #after: (callback: () => void, ms: number) => () => void;
  readonly #slots = new Map<number, Slot>();
  readonly #listeners = new Set<() => void>();
  #requests = 0;
  #owner = true;

  /** `send` writes to the playback socket; `after` calls back once after some milliseconds and returns how to cancel. */
  constructor(
    lanes: LaneSet,
    send: (message: ClientMessage) => void,
    after: (callback: () => void, ms: number) => () => void,
  ) {
    this.#lanes = lanes;
    this.#send = send;
    this.#after = after;
  }

  /** Calls `listener` whenever a lane's view changes; returns how to stop. */
  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  /** What the lane's list shows now; the same object until it changes. */
  view(lane: number): FileScopeView {
    return this.#slots.get(lane)?.view ?? IDLE;
  }

  /**
   * Wants the lane's list to be `limit` rows from `offset` of the objects `text` searches, in `sort` order, with
   * presence in at most `bins` spans per row. Calls in quick succession send only the last.
   */
  request(
    lane: number,
    text: string,
    sort: FileSortWire,
    offset: number,
    limit: number,
    bins: number,
  ): void {
    const wanted = { query: parseSearch(text).wire, sort, offset, limit, bins };
    let slot = this.#slots.get(lane);
    if (slot) Object.assign(slot, { wanted, asked: null, retries: 0 });
    else slot = { wanted, view: IDLE, asked: null, basis: null, retries: 0, cancel: null };
    this.#slots.set(lane, slot);
    this.#schedule(lane, slot, DEBOUNCE_MS);
    this.#replace(lane, slot, { ...slot.view, pending: true });
  }

  /** Stops wanting the lane's list, as when it is not shown, and tells the backend if it was ever asked on this socket. */
  release(lane: number): void {
    const slot = this.#slots.get(lane);
    if (!slot) return;
    slot.cancel?.();
    this.#slots.delete(lane);
    if (slot.basis !== null) this.#send({ type: "FileRelease", lane });
    this.#changed();
  }

  /** Says whether the viewer chooses its own frames, which alone reaches the whole file. */
  setOwner(owner: boolean): void {
    if (owner === this.#owner) return;
    this.#owner = owner;
    this.refresh();
  }

  /** The lanes changed: ask again for each list whose answer would differ. */
  refresh(): void {
    for (const [lane, slot] of this.#slots) {
      if (slot.cancel === null && this.#basisKey(lane) !== slot.basis) {
        slot.retries = 0;
        slot.asked = null;
        this.#schedule(lane, slot, DEBOUNCE_MS);
        this.#replace(lane, slot, { ...slot.view, pending: true });
      }
    }
  }

  /** The backend opened the entry on a new socket: nothing asked before was heard, so every list is asked for again. */
  reopen(): void {
    this.suspend();
    this.refresh();
  }

  /** The socket is gone: stop sending and forget what was awaited. The lists wanted stay wanted. */
  suspend(): void {
    for (const slot of this.#slots.values()) {
      slot.cancel?.();
      slot.cancel = null;
      slot.asked = null;
      slot.basis = null;
    }
  }

  /** Takes the backend's answer to a request, unless a newer request or other revisions have made it stale. */
  receive(message: Extract<ServerMessage, { type: "FileRows" | "FileScope" }>): void {
    const slot = this.#slots.get(message.lane);
    if (!slot || slot.asked !== message.request) return;
    if (message.type === "FileRows") {
      const state = this.#lanes.states[message.lane];
      if (
        readyOf(state)?.data_revision !== message.data_revision ||
        state.policyRevision !== message.policy_revision
      )
        return;
      const { total, matched, offset, rows } = message;
      slot.asked = null;
      this.#replace(message.lane, slot, {
        pending: false,
        page: { total, matched, offset, rows },
        unavailable: null,
      });
      return;
    }
    slot.asked = null;
    const unavailable = message.unavailable;
    // The backend may not yet know the viewer owns its frames again; nothing tells this client when it does.
    const retry =
      unavailable.state === "Following" && this.#owner && slot.retries < FOLLOWING_RETRIES;
    if (retry) {
      slot.retries++;
      this.#schedule(message.lane, slot, FOLLOWING_RETRY_MS);
    }
    this.#replace(message.lane, slot, { pending: retry, page: null, unavailable });
  }

  /** What the lane's answer depends on now. */
  #basis(lane: number): Basis {
    const state = this.#lanes.states[lane];
    const ready = readyOf(state);
    const classes = new Uint32Array(Math.ceil((ready?.filter.length ?? 0) / 32));
    if (ready)
      for (const index of styleOf(state.label, state.settings, ready.filter).enabled_classes)
        classes[index >> 5] |= 1 << (index & 31);
    return {
      data: ready?.data_revision ?? -1,
      policy: state?.policyRevision ?? 0,
      reading: ready?.ingest.state === "Reading",
      owner: this.#owner,
      classes: Array.from(classes),
    };
  }

  #basisKey(lane: number): string {
    return JSON.stringify(this.#basis(lane));
  }

  #schedule(lane: number, slot: Slot, ms: number): void {
    slot.cancel?.();
    slot.cancel = this.#after(() => {
      slot.cancel = null;
      if (this.#slots.get(lane) !== slot) return;
      const basis = this.#basis(lane);
      slot.basis = JSON.stringify(basis);
      slot.asked = ++this.#requests;
      const { query, sort, offset, limit, bins } = slot.wanted;
      const classes = basis.classes;
      this.#send({
        type: "FileQuery",
        lane,
        request: slot.asked,
        query,
        classes,
        sort,
        offset,
        limit,
        bins,
      });
    }, ms);
  }

  #replace(lane: number, slot: Slot, view: FileScopeView): void {
    if (this.#slots.get(lane) !== slot) return;
    slot.view = view;
    this.#changed();
  }

  #changed(): void {
    this.#listeners.forEach((listener) => listener());
  }
}
