/**
 * The leaves of the objects of the frame a lane shows, at the field paths a search names, as the backend reads them from
 * the sample. A search of Frame scope asks per sample (`ask`) and reads the answer for exactly that sample (`view`):
 * leaves of another sample are never lent to it, for the same object may hold other values there. The backend keeps
 * only the newest request, so asking for another sample supersedes one still out. A request whose answer never comes
 * (the authority gate may drop it) is sent again while a list listens, a few times, and when a list starts to listen.
 */

import {
  MAX_INSPECTION_QUERY_PATHS,
  MAX_INSPECTION_QUERY_PATH_BYTES,
  MAX_INSPECTION_REQUEST_BYTES,
} from "../generated/api";
import { utf8Bytes } from "./utf8";
import { INSPECTION_RETRY_MS, INSPECTION_RETRIES } from "./requestTiming";
import type { LaneFrame } from "../lanes/contract";
import type { ClientMessage, LeafWire, ServerMessage } from "../stream/protocol";

type Reply = Extract<ServerMessage, { type: "InspectedFields" }>;

/** An object's leaves at the paths asked, by `group.path` in lower case; a key repeats nothing, its values do. */
export type ObjectLeaves = { readonly [key: string]: readonly (string | number | bigint)[] };

/** The objects of a sample that have a leaf at the paths asked, by id. */
export type FrameLeaves = ReadonlyMap<string, ObjectLeaves>;

/**
 * What is known of the leaves of one sample: the answer, that it is awaited, or that the backend will not give it (it
 * does not hold the sample, or the search is more than it will do).
 */
export type LeavesView =
  | { readonly state: "ready"; readonly leaves: FrameLeaves }
  | { readonly state: "pending" }
  | { readonly state: "unavailable" };

const PENDING: LeavesView = { state: "pending" };
const UNAVAILABLE: LeavesView = { state: "unavailable" };
/** What a search that names nothing the backend reads, or a lane that draws nothing, has. */
const NONE: LeavesView = { state: "ready", leaves: new Map() };

/** How many answers are kept, so that stepping back and forth asks the backend once per sample. */
const KEPT = 16;
/** What a search compares: an integer exactly, `null` and a truth value as the text the reference makes of them. */
function valueOf(leaf: LeafWire): string | number | bigint {
  switch (leaf.kind) {
    case "null":
      return "null";
    case "bool":
      return leaf.value ? "true" : "false";
    case "int":
      return BigInt(leaf.value);
    case "float":
    case "text":
      return leaf.value;
  }
}

const keyOf = (
  source: number,
  revision: number,
  sample: number,
  paths: readonly string[],
): string => `${source}:${revision}:${sample}:${JSON.stringify(paths)}`;

const withinPaths = (paths: readonly string[]): boolean =>
  paths.length <= MAX_INSPECTION_QUERY_PATHS &&
  paths.every(
    (path) => utf8Bytes(path, MAX_INSPECTION_QUERY_PATH_BYTES) <= MAX_INSPECTION_QUERY_PATH_BYTES,
  );

export class FrameFields {
  readonly #send: (message: ClientMessage) => void;
  readonly #listeners = new Set<() => void>();
  /** The answers, oldest first. */
  readonly #answers = new Map<string, LeavesView>();
  readonly #after: (callback: () => void, ms: number) => () => void;
  /** The request out, with the columns of the sample it was made for. */
  #asked: {
    key: string;
    columns: unknown;
    message: Extract<ClientMessage, { type: "InspectFields" }>;
  } | null = null;
  /** Cancels the timer that sends the request out again. */
  #cancel: (() => void) | null = null;
  #retries = 0;
  #serial = 0;
  #allowed = true;
  #connected = true;
  /** A sample the backend does not answer for: asked again only when the viewer holds it anew. */
  #refused: { key: string; columns: unknown } | null = null;

  /** `send` asks the backend; `after` times the asking again. */
  constructor(
    send: (message: ClientMessage) => void,
    after: (callback: () => void, ms: number) => () => void,
  ) {
    this.#send = send;
    this.#after = after;
  }

  /**
   * Calls `listener` when a view may have changed. A list that starts to listen asks again for what it wants: a request
   * left unanswered while none did may have been dropped.
   */
  subscribe = (listener: () => void): (() => void) => {
    this.#forget();
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
      if (this.#listeners.size === 0) this.#forget();
    };
  };

  /** What is known of the leaves of the frame's sample at `paths`; the same object until that changes. */
  view(
    frame: LaneFrame | undefined,
    paths: readonly string[],
    allowed = this.#allowed,
  ): LeavesView {
    if (!frame || paths.length === 0) return NONE;
    if (!allowed || !this.#connected || !withinPaths(paths)) return UNAVAILABLE;
    const key = keyOf(frame.source, frame.dataRevision, frame.sample, paths);
    const refused = this.#refused;
    if (refused?.key === key && refused.columns === frame.columns) return UNAVAILABLE;
    return this.#answers.get(key) ?? PENDING;
  }

  /** Wants the leaves of the frame's sample at `paths`; asks the backend unless it has answered or is being awaited. */
  ask(frame: LaneFrame | undefined, paths: readonly string[], allowed = this.#allowed): void {
    if (allowed !== this.#allowed) {
      this.#allowed = allowed;
      this.#answers.clear();
      this.#refused = null;
      this.#forget();
    }
    if (!allowed || !this.#connected) {
      this.#forget();
      return;
    }
    if (!frame || paths.length === 0) {
      this.#forget();
      return;
    }
    if (!withinPaths(paths)) {
      this.#refused = null;
      this.#forget();
      return;
    }
    const { source, dataRevision, sample, columns } = frame;
    const key = keyOf(source, dataRevision, sample, paths);
    if (this.#refused?.key !== key) this.#refused = null;
    if (this.#refused?.key === key && this.#refused.columns === columns) return;
    // Another sample is wanted: the request out for this one is no longer worth sending again.
    if (this.#asked && this.#asked.key !== key) this.#forget();
    if (this.#answers.has(key)) return;
    if (this.#asked?.key === key) return;
    const message = {
      type: "InspectFields" as const,
      request: ++this.#serial,
      source,
      data_revision: dataRevision,
      sample,
      paths: [...paths],
    };
    if (
      utf8Bytes(JSON.stringify(message), MAX_INSPECTION_REQUEST_BYTES) >
      MAX_INSPECTION_REQUEST_BYTES
    ) {
      this.#refuse(key, columns);
      return;
    }
    this.#asked = { key, columns, message };
    this.#retries = 0;
    this.#send(message);
    this.#watch();
  }

  #refuse(key: string, columns: unknown): void {
    this.#refused = { key, columns };
    this.#forget();
    this.#listeners.forEach((listener) => listener());
  }

  /** Forgets the request out, and its timer. */
  #forget(): void {
    this.#asked = null;
    this.#unwatch();
  }

  #unwatch(): void {
    this.#cancel?.();
    this.#cancel = null;
  }

  /** While a list listens, sends the request out again if it stays unanswered: the authority gate may have dropped it. */
  #watch(): void {
    this.#unwatch();
    if (!this.#asked || this.#listeners.size === 0) return;
    this.#cancel = this.#after(() => {
      this.#cancel = null;
      if (!this.#asked) return;
      if (this.#retries >= INSPECTION_RETRIES) {
        this.#refuse(this.#asked.key, this.#asked.columns);
        return;
      }
      this.#retries++;
      this.#send(this.#asked.message);
      this.#watch();
    }, INSPECTION_RETRY_MS);
  }

  /** The backend's answer. */
  receive(reply: Reply): void {
    const key = keyOf(reply.source, reply.data_revision, reply.sample, reply.paths);
    const asked = this.#asked?.key === key ? this.#asked : null;
    if (!asked || asked.message.request !== reply.request) return;
    this.#forget();
    const { objects } = reply;
    if (objects === null) {
      this.#refuse(key, asked.columns);
      return;
    }
    const leaves = new Map<string, ObjectLeaves>();
    for (const { entity, items } of objects) {
      const keyed: { [key: string]: (string | number | bigint)[] } = {};
      for (const { key: name, value } of items) (keyed[name] ??= []).push(valueOf(value));
      leaves.set(entity, keyed);
    }
    this.#answers.set(key, { state: "ready", leaves });
    if (this.#answers.size > KEPT)
      this.#answers.delete(this.#answers.keys().next().value as string);
    this.#listeners.forEach((listener) => listener());
  }

  /** Connection loss abandons requests immediately; a fresh connection may ask again. */
  disconnect(): void {
    this.#connected = false;
    this.#answers.clear();
    this.#forget();
    this.#refused = null;
    this.#listeners.forEach((listener) => listener());
  }

  /** A new connection: nothing of the old one holds. */
  reconnect(): void {
    this.#connected = true;
    this.#answers.clear();
    this.#forget();
    this.#refused = null;
    this.#listeners.forEach((listener) => listener());
  }
}
