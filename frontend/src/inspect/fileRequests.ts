/**
 * What every File scope question to the backend (the objects list, one object's appearances, the events list, one
 * event) shares, kept once. A viewer says per lane what it wants (`want`) and reads the latest answer (`view`); a
 * `FileRequests` keeps the backend asked for exactly that. It asks again when the answer would change: a new Ready
 * revision, a new policy revision, the file's ingest ending and the viewer becoming the owner of its frames again, for
 * the backend announces none of these. A list waits for a pause in the changes and keeps its page while the next is
 * awaited; a lookup asks at once and shows nothing until it is answered. An answer is matched to its request by id, so
 * the host numbers the requests of all kinds in one sequence; an older request's answer, one made under other
 * revisions, or one that is not what was asked about is dropped. A page that could not be refreshed stays, flagged by
 * `unavailable`, while the file's data is what it showed. A follower is refused, and the question stays open: the
 * backend holds it and answers it again under the same id when the viewer becomes the owner, so the client never
 * retries.
 */

import type { FileBody, FileScopeUnavailable } from "../generated/api";
import type { LaneState } from "../lanes/contract";
import type { LaneSet, ReadyStatus } from "../lanes/lanes";
import type { ClientMessage, ServerMessage } from "../stream/protocol";

/** How long a change to a wanted list waits for another before it is sent. */
export const DEBOUNCE_MS = 150;

/** The lane's Ready status, if it is ready. */
export const readyOf = (state: LaneState | undefined): ReadyStatus | undefined =>
  state && state.status !== "plain" && state.status.status === "Ready" ? state.status : undefined;

/** What the backend's answer depends on. */
export type Basis = { data: number; policy: number; reading: boolean; owner: boolean };

/** What `lane`'s answers depend on now, for a viewer that `owner`s its frames or follows. */
export function basisOf(lanes: LaneSet, lane: number, owner: boolean): Basis {
  const state = lanes.states[lane];
  const ready = readyOf(state);
  return {
    data: ready?.data_revision ?? -1,
    policy: state?.policyRevision ?? 0,
    reading: ready?.ingest.state === "Reading",
    owner,
  };
}

/** The backend's answer to a File scope request. */
export type FileMessage = Extract<ServerMessage, { type: "File" }>;

/**
 * What a lane's request shows now. Replaced, never mutated, when it changes. The page is out of date when both it and
 * `unavailable` are set: a refresh failed and the page is that of the last answer, which the next one replaces.
 */
export type FileView<T> = {
  /** An answer is awaited, or the question is about to be asked again. */
  pending: boolean;
  /** The last page accepted; `null` when none, or when the backend has none to give. */
  page: T | null;
  /** Why the backend has no page; `null` otherwise. */
  unavailable: FileScopeUnavailable | null;
};

/** What a lane shows when nothing is wanted of it. */
export const IDLE: FileView<never> = { pending: false, page: null, unavailable: null };

/** What a `FileRequests` needs from the viewer's File scope. */
export type FileRequestsHost = {
  lanes: LaneSet;
  /** Writes to the playback socket. */
  send(message: ClientMessage): void;
  /** Calls back once after some milliseconds and returns how to cancel. */
  after(callback: () => void, ms: number): () => void;
  /** Whether the viewer chooses its own frames, which alone reaches the whole file. */
  owner(): boolean;
  /** The next request id, from one sequence for every kind. */
  request(): number;
  /** Called when a view changes. */
  changed(): void;
  /** Called when a lane that was asked about no longer has this kind wanted. */
  released(lane: number): void;
};

/** How one kind of request is asked and its answer read: `W` is what is wanted, `B` what the answer depends on, `T` its page. */
export type FileRequestSpec<K extends FileBody["kind"], W, B extends object, T> = {
  kind: K;
  /** A list waits `DEBOUNCE_MS` and keeps its page while asking; a lookup asks at once and has no page until answered. */
  shape: "list" | "lookup";
  /** What the answer depends on besides the lane's data and policy revisions; compared as JSON. */
  basis(lanes: LaneSet, lane: number, owner: boolean): B;
  message(lane: number, request: number, wanted: W, basis: B): ClientMessage;
  /** The page `body` makes of what was asked, under data revision `data`; `null` when it is not about what was asked. */
  page(body: Extract<FileBody, { kind: K }>, asked: W, data: number): T | null;
  /** When the accepted page asks for a new question: after how long, and whether the view shows it pending. */
  again?(page: T, asked: W, wanted: W): { ms: number; pending: boolean } | null;
};

/** What the File scope drives of every kind alike. */
export interface LaneRequests {
  wants(lane: number): boolean;
  refresh(): void;
  suspend(): void;
  receive(message: FileMessage): void;
}

type Slot<W, T> = {
  wanted: W;
  /** What the request awaited was made of. */
  asking: W;
  view: FileView<T>;
  /** The data revision of the page the view holds; a page of another revision is not kept through a failure. */
  pageData: number | null;
  /** The request the answer awaited belongs to; `null` when none is awaited. */
  asked: number | null;
  /** What the last request was made under, as JSON; `null` when none stands. */
  basis: string | null;
  cancel: (() => void) | null;
};

/** One kind of File scope request, per lane. */
export class FileRequests<
  K extends FileBody["kind"],
  W,
  B extends object,
  T,
> implements LaneRequests {
  readonly #spec: FileRequestSpec<K, W, B, T>;
  readonly #host: FileRequestsHost;
  readonly #slots = new Map<number, Slot<W, T>>();

  constructor(spec: FileRequestSpec<K, W, B, T>, host: FileRequestsHost) {
    this.#spec = spec;
    this.#host = host;
  }

  /** What the lane shows now; the same object until it changes. */
  view(lane: number): FileView<T> {
    return this.#slots.get(lane)?.view ?? IDLE;
  }

  /** What the lane is wanted to be; `undefined` when nothing is. */
  wanted(lane: number): W | undefined {
    return this.#slots.get(lane)?.wanted;
  }

  /** Whether anything is wanted of the lane. */
  wants(lane: number): boolean {
    return this.#slots.has(lane);
  }

  /** What the lane's answer depends on; one asked under another differs. */
  basis(lane: number): string {
    return this.#key(lane);
  }

  /** Wants the lane to be `wanted`: a list sends the last of quick calls, a lookup asks at once. */
  want(lane: number, wanted: W): void {
    const held = this.#slots.get(lane);
    if (this.#spec.shape === "lookup") {
      held?.cancel?.();
      const slot = this.#fresh(wanted);
      this.#slots.set(lane, slot);
      this.#ask(lane, slot);
      return;
    }
    // A list is not asked again only for being wanted again: the answer in flight is stale and the pause starts over.
    const slot = held ?? this.#fresh(wanted);
    if (held) Object.assign(held, { wanted, asked: null });
    this.#slots.set(lane, slot);
    this.#schedule(lane, slot, DEBOUNCE_MS);
    this.#replace(slot, { ...slot.view, pending: true });
  }

  /**
   * Changes what the lane is wanted to be without a new question: the next one, a refresh or a re-ask included, carries
   * it. With `askIn`, asks after that long unless a question is out or already due.
   */
  amend(lane: number, wanted: W, askIn: number | null): void {
    const slot = this.#slots.get(lane);
    if (!slot) return;
    slot.wanted = wanted;
    if (askIn !== null && slot.cancel === null && slot.asked === null)
      this.#schedule(lane, slot, askIn);
  }

  /** Asks for the wanted list now, instead of when the pause for more changes ends, and again if it was asked already. */
  flush(lane: number): void {
    const slot = this.#slots.get(lane);
    if (!slot) return;
    slot.cancel?.();
    slot.cancel = null;
    this.#ask(lane, slot);
    this.#replace(slot, { ...slot.view, pending: true });
  }

  /** Stops wanting the lane, and tells the host if it was ever asked on this socket. */
  release(lane: number): void {
    const slot = this.#slots.get(lane);
    if (!slot) return;
    slot.cancel?.();
    this.#slots.delete(lane);
    if (slot.view !== IDLE) this.#host.changed();
    if (slot.basis !== null) this.#host.released(lane);
  }

  /** The lanes changed: ask again for each lane whose answer would differ. */
  refresh(): void {
    for (const [lane, slot] of this.#slots) {
      if (slot.cancel !== null || this.#key(lane) === slot.basis) continue;
      if (this.#spec.shape === "lookup") {
        this.#ask(lane, slot);
        continue;
      }
      slot.asked = null;
      this.#schedule(lane, slot, DEBOUNCE_MS);
      this.#replace(slot, { ...slot.view, pending: true });
    }
  }

  /** The socket is gone: stop sending and forget what was awaited. What is wanted stays wanted. */
  suspend(): void {
    for (const slot of this.#slots.values()) {
      slot.cancel?.();
      Object.assign(slot, { cancel: null, asked: null, basis: null });
    }
  }

  /** Takes the backend's answer, unless a newer request or other revisions have made it stale. */
  receive(message: FileMessage): void {
    const { lane, request, answer } = message;
    const slot = this.#slots.get(lane);
    if (!slot || slot.asked !== request) return;
    const state = this.#host.lanes.states[lane];
    if (answer.state === "Found") {
      if (answer.body.kind !== this.#spec.kind) return;
      if (
        readyOf(state)?.data_revision !== answer.data_revision ||
        state.policyRevision !== answer.policy_revision
      )
        return;
      const body = answer.body as Extract<FileBody, { kind: K }>;
      const page = this.#spec.page(body, slot.asking, answer.data_revision);
      if (page === null) return;
      slot.asked = null;
      slot.pageData = answer.data_revision;
      const again = this.#spec.again?.(page, slot.asking, slot.wanted) ?? null;
      if (again) this.#schedule(lane, slot, again.ms);
      this.#replace(slot, { pending: again?.pending ?? false, page, unavailable: null });
      return;
    }
    const { unavailable } = answer;
    // The backend holds a question refused as Following and answers it again, under the same id, once the viewer owns.
    if (unavailable.state !== "Following") slot.asked = null;
    const keep = unavailable.state === "Failed" && slot.pageData === readyOf(state)?.data_revision;
    this.#replace(slot, { pending: false, page: keep ? slot.view.page : null, unavailable });
  }

  #fresh(wanted: W): Slot<W, T> {
    return {
      wanted,
      asking: wanted,
      view: IDLE,
      pageData: null,
      asked: null,
      basis: null,
      cancel: null,
    };
  }

  #key(lane: number): string {
    return JSON.stringify(this.#spec.basis(this.#host.lanes, lane, this.#host.owner()));
  }

  #schedule(lane: number, slot: Slot<W, T>, ms: number): void {
    slot.cancel?.();
    slot.cancel = this.#host.after(() => {
      slot.cancel = null;
      this.#ask(lane, slot);
    }, ms);
  }

  #ask(lane: number, slot: Slot<W, T>): void {
    if (this.#slots.get(lane) !== slot) return;
    const basis = this.#spec.basis(this.#host.lanes, lane, this.#host.owner());
    slot.basis = JSON.stringify(basis);
    slot.asked = this.#host.request();
    slot.asking = slot.wanted;
    this.#host.send(this.#spec.message(lane, slot.asked, slot.wanted, basis));
    if (this.#spec.shape === "lookup")
      this.#replace(slot, { pending: true, page: null, unavailable: slot.view.unavailable });
  }

  #replace(slot: Slot<W, T>, view: FileView<T>): void {
    slot.view = view;
    this.#host.changed();
  }
}
