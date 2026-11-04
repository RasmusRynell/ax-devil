/**
 * File scope as the client asks for it: the objects a lane's whole overlay file holds, listed by the backend. A viewer
 * says what list it wants per lane (`request`) and reads the latest answer (`view`); this keeps the backend asked for
 * exactly that list. It asks again when the answer would change: a new Ready revision, a new policy revision, the
 * class toggles, the file's ingest ending and the viewer becoming the owner of its frames again, for the backend
 * announces none of these. Answers of an older request, or made under other revisions, are dropped. The backend frees
 * what it kept for a lane when the list and the object's appearances have both been let go of. Where one object
 * appears is asked and kept the same way, by `appearances`, which shares the lane's revisions, owner and socket but
 * not its class toggles.
 */

import type { FileRowWire, FileScopeUnavailable, FileSortWire, Located } from "../generated/api";
import { Appearances } from "./appearances";
import { basisOf, FOLLOWING_RETRIES, FOLLOWING_RETRY_MS, readyOf } from "./fileBasis";
import { styleOf } from "./laneStyle";
import { parseSearch } from "../search/query";
import type { LaneSet } from "./lanes";
import type { ClientMessage, ServerMessage } from "./protocol";

/** The lane's class toggles as the bitmask words a query sends: bit `i` of word `i >> 5` is class `i`. */
function classesOf(lanes: LaneSet, lane: number): number[] {
  const state = lanes.states[lane];
  const ready = readyOf(state);
  const words = new Uint32Array(Math.ceil((ready?.filter.length ?? 0) / 32));
  if (ready)
    for (const index of styleOf(state.label, state.settings, ready.filter).enabled_classes)
      words[index >> 5] |= 1 << (index & 31);
  return Array.from(words);
}

/** What the list's answer depends on: the lane's basis and its class toggles. */
function listBasis(lanes: LaneSet, lane: number, owner: boolean) {
  return { ...basisOf(lanes, lane, owner), classes: classesOf(lanes, lane) };
}

/** How long a change to the wanted list waits for another before it is sent. */
const DEBOUNCE_MS = 150;
/** One page of a lane's list. */
export type FilePage = {
  /** How many objects the video shows that the class toggles keep. */
  total: number;
  /** How many of them the search matches. */
  matched: number;
  /** The position in the list of the first of `rows`. */
  offset: number;
  rows: readonly FileRowWire[];
  /** Where the object asked about is in the whole list, whichever page this is; `null` when none was asked about. */
  located: Located | null;
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
  locate: string | null;
};

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
  /** Where the object a lane's page is about appears. */
  readonly appearances: Appearances;
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
    this.appearances = new Appearances(
      lanes,
      send,
      after,
      () => this.#owner,
      () => this.#changed(),
      (lane) => {
        if (!this.#slots.has(lane)) this.#send({ type: "FileRelease", lane });
      },
    );
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
   * presence in at most `bins` spans per row. `locate` names an object whose row in the whole list the page tells too.
   * Calls in quick succession send only the last.
   */
  request(
    lane: number,
    text: string,
    sort: FileSortWire,
    offset: number,
    limit: number,
    bins: number,
    locate: string | null = null,
  ): void {
    const wanted = { query: parseSearch(text).wire, sort, offset, limit, bins, locate };
    let slot = this.#slots.get(lane);
    if (slot) Object.assign(slot, { wanted, asked: null, retries: 0 });
    else slot = { wanted, view: IDLE, asked: null, basis: null, retries: 0, cancel: null };
    this.#slots.set(lane, slot);
    this.#schedule(lane, slot, DEBOUNCE_MS);
    this.#replace(lane, slot, { ...slot.view, pending: true });
  }

  /**
   * Names the object whose row the lane's list page tells from now on, as the viewer's cursor moves. Nothing is sent
   * for it; the next question, a refresh or a re-ask included, carries it. If the answer awaited was asked about
   * another object and does not hold this one, it is asked again once that answer is in.
   */
  locating(lane: number, locate: string | null): void {
    const slot = this.#slots.get(lane);
    if (slot) slot.wanted.locate = locate;
  }

  /** Stops wanting the lane's list, as when it is not shown, and tells the backend if it was ever asked on this socket. */
  release(lane: number): void {
    const slot = this.#slots.get(lane);
    if (!slot) return;
    slot.cancel?.();
    this.#slots.delete(lane);
    if (slot.basis !== null && !this.appearances.wants(lane))
      this.#send({ type: "FileRelease", lane });
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
    this.appearances.refresh();
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
    this.appearances.suspend();
    for (const slot of this.#slots.values()) {
      slot.cancel?.();
      slot.cancel = null;
      slot.asked = null;
      slot.basis = null;
    }
  }

  /** Takes the backend's answer to a request, unless a newer request or other revisions have made it stale. */
  receive(
    message: Extract<ServerMessage, { type: "FileRows" | "FileScope" | "Appearances" }>,
  ): void {
    if (message.type === "Appearances") {
      this.appearances.receive(message);
      return;
    }
    const slot = this.#slots.get(message.lane);
    if (!slot || slot.asked !== message.request) return;
    if (message.type === "FileRows") {
      const state = this.#lanes.states[message.lane];
      if (
        readyOf(state)?.data_revision !== message.data_revision ||
        state.policyRevision !== message.policy_revision
      )
        return;
      const { total, matched, offset, rows, located } = message;
      slot.asked = null;
      // The cursor moved to an object this page neither locates nor holds while the question was out.
      const key = slot.wanted.locate;
      const lost = key !== null && located?.id !== key && !rows.some(({ id }) => id === key);
      if (lost) this.#schedule(message.lane, slot, DEBOUNCE_MS);
      this.#replace(message.lane, slot, {
        pending: lost,
        page: { total, matched, offset, rows, located },
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

  #basisKey(lane: number): string {
    return JSON.stringify(listBasis(this.#lanes, lane, this.#owner));
  }

  #schedule(lane: number, slot: Slot, ms: number): void {
    slot.cancel?.();
    slot.cancel = this.#after(() => {
      slot.cancel = null;
      if (this.#slots.get(lane) !== slot) return;
      const basis = listBasis(this.#lanes, lane, this.#owner);
      slot.basis = JSON.stringify(basis);
      slot.asked = ++this.#requests;
      const { query, sort, offset, limit, bins, locate } = slot.wanted;
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
        locate,
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
