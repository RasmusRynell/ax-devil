/**
 * The leaves of the objects of the frame a lane shows, at the field paths a search names, as the backend reads them from
 * the sample. A search of Frame scope asks per sample (`ask`) and reads the answer for exactly that sample (`view`):
 * leaves of another sample are never lent to it, for the same object may hold other values there. The backend keeps
 * only the newest request, so asking for another sample supersedes one still out. A request is answered once, with its
 * leaves or as unavailable, and stays pending until then.
 */

import {
  MAX_INSPECTION_QUERY_PATHS,
  MAX_INSPECTION_QUERY_PATH_BYTES,
  MAX_INSPECTION_REQUEST_BYTES,
} from "../generated/api";
import { utf8Bytes } from "./utf8";
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
  /** One current attempt, including its terminal refusal; abandonment drops all of it. */
  #request: {
    key: string;
    columns: unknown;
    message: Extract<ClientMessage, { type: "InspectFields" }>;
    state: "pending" | "unavailable";
  } | null = null;
  #serial = 0;
  #allowed = true;
  #connected = true;

  /** `send` asks the backend. */
  constructor(send: (message: ClientMessage) => void) {
    this.#send = send;
  }

  /** Calls `listener` when a view may have changed. */
  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
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
    const request = this.#request;
    if (
      request?.key === key &&
      request.columns === frame.columns &&
      request.state === "unavailable"
    )
      return UNAVAILABLE;
    return this.#answers.get(key) ?? PENDING;
  }

  /** Wants the leaves of the frame's sample at `paths`; asks the backend unless it has answered or is being awaited. */
  ask(frame: LaneFrame | undefined, paths: readonly string[], allowed = this.#allowed): void {
    if (allowed !== this.#allowed) {
      this.#allowed = allowed;
      this.#answers.clear();
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
      this.#forget();
      return;
    }
    const { source, dataRevision, sample, columns } = frame;
    const key = keyOf(source, dataRevision, sample, paths);
    if (this.#request && (this.#request.key !== key || this.#request.columns !== columns))
      this.#forget();
    if (this.#answers.has(key) || this.#request) return;
    const message = {
      type: "InspectFields" as const,
      request: ++this.#serial,
      source,
      data_revision: dataRevision,
      sample,
      paths: [...paths],
    };
    this.#request = { key, columns, message, state: "pending" };
    if (
      utf8Bytes(JSON.stringify(message), MAX_INSPECTION_REQUEST_BYTES) >
      MAX_INSPECTION_REQUEST_BYTES
    ) {
      this.#refuse();
      return;
    }
    this.#send(message);
  }

  #refuse(): void {
    if (!this.#request) return;
    this.#request.state = "unavailable";
    this.#listeners.forEach((listener) => listener());
  }

  /** Abandons the complete attempt, including a refusal. */
  #forget(): void {
    this.#request = null;
  }

  /** The backend's answer. */
  receive(reply: Reply): void {
    const key = keyOf(reply.source, reply.data_revision, reply.sample, reply.paths);
    const request = this.#request;
    if (
      request?.state !== "pending" ||
      request.key !== key ||
      request.message.request !== reply.request
    )
      return;
    const { objects } = reply;
    if (objects === null) {
      this.#refuse();
      return;
    }
    this.#forget();
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
    this.#listeners.forEach((listener) => listener());
  }

  /** A new connection: nothing of the old one holds. */
  reconnect(): void {
    this.#connected = true;
    this.#answers.clear();
    this.#forget();
    this.#listeners.forEach((listener) => listener());
  }
}
