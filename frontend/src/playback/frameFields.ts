/**
 * The leaves of the objects of the frame a lane shows, at the field paths a search names, as the backend reads them from
 * the sample. A search of Frame scope asks per sample (`ask`) and reads the answer for exactly that sample (`view`):
 * leaves of another sample are never lent to it, for the same object may hold other values there. The backend keeps
 * only the newest request, so asking for another sample supersedes one still out. A request whose answer never comes
 * (the authority gate may drop it) is asked again when a list starts to listen.
 */

import type { LaneFrame } from "./contract";
import type { ClientMessage, LeafWire, ServerMessage } from "./protocol";

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
): string => `${source}:${revision}:${sample}:${paths.join("\n")}`;

export class FrameFields {
  readonly #send: (message: ClientMessage) => void;
  readonly #listeners = new Set<() => void>();
  /** The answers, oldest first. */
  readonly #answers = new Map<string, LeavesView>();
  /** The request out, with the columns of the sample it was made for. */
  #asked: { key: string; columns: unknown } | null = null;
  /** A sample the backend does not answer for: asked again only when the viewer holds it anew. */
  #refused: { key: string; columns: unknown } | null = null;

  /** `send` asks the backend. */
  constructor(send: (message: ClientMessage) => void) {
    this.#send = send;
  }

  /**
   * Calls `listener` when a view may have changed. A list that starts to listen asks again for what it wants: a request
   * left unanswered while none did may have been dropped.
   */
  subscribe = (listener: () => void): (() => void) => {
    this.#asked = null;
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  /** What is known of the leaves of the frame's sample at `paths`; the same object until that changes. */
  view(frame: LaneFrame | undefined, paths: readonly string[]): LeavesView {
    if (!frame || paths.length === 0) return NONE;
    const key = keyOf(frame.source, frame.dataRevision, frame.sample, paths);
    const refused = this.#refused;
    if (refused?.key === key && refused.columns === frame.columns) return UNAVAILABLE;
    return this.#answers.get(key) ?? PENDING;
  }

  /** Wants the leaves of the frame's sample at `paths`; asks the backend unless it has answered or is being awaited. */
  ask(frame: LaneFrame | undefined, paths: readonly string[]): void {
    if (!frame || paths.length === 0) {
      this.#asked = null;
      return;
    }
    const { source, dataRevision, sample, columns } = frame;
    const key = keyOf(source, dataRevision, sample, paths);
    if (this.#answers.has(key)) return;
    if (this.#refused?.key === key && this.#refused.columns === columns) return;
    if (this.#asked?.key === key) return;
    this.#asked = { key, columns };
    this.#send({
      type: "InspectFields",
      source,
      data_revision: dataRevision,
      sample,
      paths: [...paths],
    });
  }

  /** The backend's answer. */
  receive(reply: Reply): void {
    const key = keyOf(reply.source, reply.data_revision, reply.sample, reply.paths);
    const asked = this.#asked?.key === key ? this.#asked : null;
    if (asked) this.#asked = null;
    const { objects } = reply;
    if (objects === null) {
      if (asked) this.#refused = { key, columns: asked.columns };
      this.#listeners.forEach((listener) => listener());
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

  /** A new connection: nothing of the old one holds. */
  reconnect(): void {
    this.#answers.clear();
    this.#asked = null;
    this.#refused = null;
    this.#listeners.forEach((listener) => listener());
  }
}
