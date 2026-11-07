/**
 * The leaves of the objects of the frame a lane shows, at the field paths a search names, as the backend reads them from
 * the sample. A search of Frame scope asks per sample (`ask`) and reads the latest answer (`view`); the backend keeps
 * only the newest request, so asking for another sample supersedes one still out. While a newer sample's answer is
 * out `view` keeps the last one for the same paths, whose objects are mostly the same ones.
 */

import type { LaneFrame } from "./contract";
import type { ClientMessage, LeafWire, ServerMessage } from "./protocol";

type Reply = Extract<ServerMessage, { type: "InspectedFields" }>;

/** An object's leaves at the paths asked, by `group.path` in lower case; a key repeats nothing, its values do. */
export type ObjectLeaves = { readonly [key: string]: readonly (string | number | bigint)[] };

/** The objects of a sample that have a leaf at the paths asked, by id. */
export type FrameLeaves = ReadonlyMap<string, ObjectLeaves>;

/** How many (source, revision, paths) sets are kept. */
const KEPT = 8;

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

const setOf = (source: number, revision: number, paths: readonly string[]): string =>
  `${source}:${revision}:${paths.join("\n")}`;

export class FrameFields {
  readonly #send: (message: ClientMessage) => void;
  readonly #listeners = new Set<() => void>();
  /** The newest answer for each set of paths of a source revision, with the sample it is of. */
  readonly #answers = new Map<string, { sample: number; leaves: FrameLeaves }>();
  /** The request out, or the last one answered, with the columns of the sample it was made for. */
  #asked: { set: string; sample: number; columns: unknown } | null = null;
  /** A sample the backend said it does not hold: it is asked again only when the viewer holds it anew. */
  #refused: { set: string; sample: number; columns: unknown } | null = null;

  /** `send` asks the backend. */
  constructor(send: (message: ClientMessage) => void) {
    this.#send = send;
  }

  /** Calls `listener` when a view may have changed. */
  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  /** The leaves last answered for `paths` of the frame's source, whichever sample; `null` until one is. */
  view(frame: LaneFrame | undefined, paths: readonly string[]): FrameLeaves | null {
    if (!frame || paths.length === 0) return null;
    return this.#answers.get(setOf(frame.source, frame.dataRevision, paths))?.leaves ?? null;
  }

  /** Wants the leaves of the frame's sample at `paths`; asks the backend unless it has answered or been asked. */
  ask(frame: LaneFrame | undefined, paths: readonly string[]): void {
    if (!frame || paths.length === 0) return;
    const { source, dataRevision, sample, columns } = frame;
    const set = setOf(source, dataRevision, paths);
    const same = (held: { set: string; sample: number; columns: unknown } | null) =>
      held?.set === set && held.sample === sample;
    if (this.#answers.get(set)?.sample === sample || same(this.#asked)) return;
    if (same(this.#refused) && this.#refused?.columns === columns) return;
    this.#asked = { set, sample, columns };
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
    const set = setOf(reply.source, reply.data_revision, reply.paths);
    const { objects, sample } = reply;
    if (objects === null) {
      if (this.#asked?.set === set && this.#asked.sample === sample) {
        this.#refused = this.#asked;
        this.#asked = null;
      }
      return;
    }
    const leaves = new Map<string, ObjectLeaves>();
    for (const { entity, items } of objects) {
      const keyed: { [key: string]: (string | number | bigint)[] } = {};
      for (const { key, value } of items) (keyed[key] ??= []).push(valueOf(value));
      leaves.set(entity, keyed);
    }
    this.#answers.delete(set);
    this.#answers.set(set, { sample, leaves });
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
