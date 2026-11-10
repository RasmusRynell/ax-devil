/**
 * File scope as the client asks for it: the objects a lane's whole overlay file holds, listed by the backend. A viewer
 * says what list it wants per lane (`request`) and reads the latest answer (`view`); the rules of asking, and of asking
 * again, are those of `FileRequests`, and the list also asks again when the lane's class toggles change. This owns the
 * one request sequence, the owner flag and the listeners that the objects list, the object's appearances and the
 * events share, and tells the backend to free what it kept for a lane when none of them wants it any more.
 */

import type { FileRowWire, FileSortWire, Located } from "../generated/api";
import { Appearances } from "./appearances";
import { FileEvents } from "./fileEvents";
import {
  basisOf,
  DEBOUNCE_MS,
  FileRequests,
  readyOf,
  type Basis,
  type FileMessage,
  type FileRequestSpec,
  type FileRequestsHost,
  type FileView,
  type LaneRequests,
} from "./fileRequests";
import { styleOf } from "./laneStyle";
import { parseSearch } from "../search/query";
import type { LaneSet } from "./lanes";
import type { ClientMessage } from "./protocol";

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

/** What a lane's list shows now. */
export type FileScopeView = FileView<FilePage>;

/** What a lane's list is wanted to be. */
type Wanted = {
  query: ReturnType<typeof parseSearch>["wire"];
  sort: FileSortWire;
  offset: number;
  limit: number;
  bins: number;
  locate: string | null;
};

const OBJECTS: FileRequestSpec<"Objects", Wanted, Basis & { classes: number[] }, FilePage> = {
  kind: "Objects",
  shape: "list",
  basis: (lanes, lane, owner) => ({
    ...basisOf(lanes, lane, owner),
    classes: classesOf(lanes, lane),
  }),
  message: (lane, request, { query, sort, offset, limit, bins, locate }, { classes }) => ({
    type: "FileQuery",
    lane,
    request,
    query,
    classes,
    sort,
    offset,
    limit,
    bins,
    locate,
  }),
  page: ({ total, matched, offset, rows, located }) => ({ total, matched, offset, rows, located }),
  // The cursor moved to an object this page neither locates nor holds while the question was out.
  again: ({ rows, located }, _, { locate }) =>
    locate !== null && located?.id !== locate && !rows.some(({ id }) => id === locate)
      ? { ms: DEBOUNCE_MS, pending: true }
      : null,
};

export class FileScope {
  readonly #send: (message: ClientMessage) => void;
  readonly #listeners = new Set<() => void>();
  readonly #objects: FileRequests<"Objects", Wanted, Basis & { classes: number[] }, FilePage>;
  readonly #requests: LaneRequests[];
  /** Where the object a lane's page is about appears. */
  readonly appearances: Appearances;
  /** The events the lane's file holds. */
  readonly events: FileEvents;
  #ids = 0;
  #owner = true;

  /** `send` writes to the playback socket; `after` calls back once after some milliseconds and returns how to cancel. */
  constructor(
    lanes: LaneSet,
    send: (message: ClientMessage) => void,
    after: (callback: () => void, ms: number) => () => void,
  ) {
    this.#send = send;
    const host: FileRequestsHost = {
      lanes,
      send,
      after,
      owner: () => this.#owner,
      request: () => ++this.#ids,
      changed: () => this.#listeners.forEach((listener) => listener()),
      released: (lane) => this.#freed(lane),
    };
    this.#objects = new FileRequests(OBJECTS, host);
    this.appearances = new Appearances(host);
    this.events = new FileEvents(host);
    this.#requests = [this.#objects, this.appearances.requests, this.events.list, this.events.one];
  }

  /** Calls `listener` whenever a lane's view changes; returns how to stop. */
  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  /** What the lane's list shows now; the same object until it changes. */
  view(lane: number): FileScopeView {
    return this.#objects.view(lane);
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
    this.#objects.want(lane, { query: parseSearch(text).wire, sort, offset, limit, bins, locate });
  }

  /** Asks for the wanted list now, instead of when the pause for more changes ends, and again if it was asked already. */
  flush(lane: number): void {
    this.#objects.flush(lane);
  }

  /**
   * Names the object whose row the lane's list page tells from now on, as the viewer's cursor moves. Nothing is sent
   * for it; the next question, a refresh or a re-ask included, carries it. If the answer awaited was asked about
   * another object and does not hold this one, it is asked again once that answer is in.
   */
  locating(lane: number, locate: string | null): void {
    const wanted = this.#objects.wanted(lane);
    if (wanted) this.#objects.amend(lane, { ...wanted, locate }, null);
  }

  /** What the lane's list membership depends on (class toggles, data and policy revision); a list asked under another differs. */
  basis(lane: number): string {
    return this.#objects.basis(lane);
  }

  /** Stops wanting the lane's list, as when it is not shown, and tells the backend if it was ever asked on this socket. */
  release(lane: number): void {
    this.#objects.release(lane);
  }

  /** Says whether the viewer chooses its own frames, which alone reaches the whole file. */
  setOwner(owner: boolean): void {
    if (owner === this.#owner) return;
    this.#owner = owner;
    this.refresh();
  }

  /** The lanes changed: ask again for each list whose answer would differ. */
  refresh(): void {
    for (const requests of this.#requests) requests.refresh();
  }

  /** The backend opened the entry on a new socket: nothing asked before was heard, so every list is asked for again. */
  reopen(): void {
    this.suspend();
    this.refresh();
  }

  /** The socket is gone: stop sending and forget what was awaited. The lists wanted stay wanted. */
  suspend(): void {
    for (const requests of this.#requests) requests.suspend();
  }

  /** Takes the backend's answer to a request, unless a newer request or other revisions have made it stale. */
  receive(message: FileMessage): void {
    for (const requests of this.#requests) requests.receive(message);
  }

  /** Tells the backend to free what it kept for the lane, when nothing of it is wanted any more. */
  #freed(lane: number): void {
    if (!this.#requests.some((requests) => requests.wants(lane)))
      this.#send({ type: "FileRelease", lane });
  }
}
