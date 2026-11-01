/** The catalogs a session has asked the backend for, compiled once per id and shared by every lane that uses them. */

import { compileCatalog, locate, type CompiledCatalog } from "../present";

/** How many diagnostics of a failed compile the lane's notice lists. */
const SHOWN_DIAGNOSTICS = 5;

/** What a lane may draw with. `catalog` is the last compile that worked; `problem` says why a newer document failed. */
export type CatalogEntry =
  | { state: "loading" }
  | { state: "ready"; catalog?: CompiledCatalog; problem?: string };

type Held = { revision: number; entry: CatalogEntry };

export class Catalogs {
  readonly #request: (id: string) => void;
  readonly #held = new Map<string, Held>();

  /** `request` asks the backend for a catalog document; it is called once per id. */
  constructor(request: (id: string) => void) {
    this.#request = request;
  }

  /** The catalog `id`, asking for it first when this session has not. */
  get(id: string): CatalogEntry {
    let held = this.#held.get(id);
    if (!held) {
      held = { revision: 0, entry: { state: "loading" } };
      this.#held.set(id, held);
      this.#request(id);
    }
    return held.entry;
  }

  /** Forgets the requests the backend never answered, as when its connection ended; the compiles stay. */
  reconnect(): void {
    for (const [id, held] of this.#held) if (held.entry.state === "loading") this.#held.delete(id);
  }

  /**
   * Takes the backend's answer. A newer revision replaces the document; one that does not compile, or no document at
   * all, leaves the last compile in place and says why. Reports whether the entry changed.
   */
  receive(id: string, revision: number, document: string | null): boolean {
    const held = this.#held.get(id);
    if (!held || (held.entry.state === "ready" && revision <= held.revision && document !== null))
      return false;
    const last = held.entry.state === "ready" ? held.entry.catalog : undefined;
    held.revision = revision;
    if (document === null) {
      held.entry = { state: "ready", catalog: last, problem: `There is no catalog ${id}` };
      return true;
    }
    const result = compileCatalog(document);
    held.entry = result.ok
      ? { state: "ready", catalog: result.catalog }
      : {
          state: "ready",
          catalog: last,
          problem: result.diagnostics
            .slice(0, SHOWN_DIAGNOSTICS)
            .map((d) => {
              const at = locate(d, document);
              return `${d.pointer}${at ? ` (${at.line}:${at.column})` : ""}: ${d.message}`;
            })
            .join("\n"),
        };
    return true;
  }
}
