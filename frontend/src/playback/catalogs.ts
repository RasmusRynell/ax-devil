/** The catalogs a session has asked the backend for, compiled once per id and shared by every lane that uses them. */

import { compileCatalog, locate, type CompiledCatalog, type Diagnostic } from "../present";
import type { CatalogInfoWire } from "./protocol";

/** How many diagnostics of a failed compile the lane's notice lists. */
const SHOWN_DIAGNOSTICS = 5;

/** What a lane may draw with. `catalog` is the last compile that worked; `problem` says why a newer document failed. */
type CatalogEntry =
  | { state: "loading" }
  | { state: "ready"; catalog?: CompiledCatalog; problem?: string };

/** What the backend last said of a catalog: revisions restart with the backend, so the text counts too. */
type Reply = { revision: number; document: string | null; problem: string | null };

type Held = { reply?: Reply; entry: CatalogEntry };

/** The diagnostics of a failed compile, one per line with the document position when it has one. */
function describe(diagnostics: readonly Diagnostic[], document: string): string {
  return diagnostics
    .slice(0, SHOWN_DIAGNOSTICS)
    .map((d) => {
      const at = locate(d, document);
      return `${d.pointer}${at ? ` (${at.line}:${at.column})` : ""}: ${d.message}`;
    })
    .join("\n");
}

export class Catalogs {
  readonly #request: (id: string) => void;
  readonly #held = new Map<string, Held>();
  /** The revision of the document each catalog's last successful compile came from. */
  readonly #compiled = new Map<string, number>();
  #listing: readonly CatalogInfoWire[] = [];

  /** `request` asks the backend for a catalog document, which also subscribes the connection to its changes. */
  constructor(request: (id: string) => void) {
    this.#request = request;
  }

  /** The catalogs a lane may choose, as the backend last listed them. */
  get listing(): readonly CatalogInfoWire[] {
    return this.#listing;
  }

  /**
   * The revision of the document the catalog `id` is drawn from; absent until one compiled, and while the backend
   * lists the catalog as gone or with a problem, which it does before it sends the catalog's own update.
   */
  compiledRevision(id: string): number | undefined {
    const listed = this.#listing.find((catalog) => catalog.id === id);
    return listed && listed.problem === undefined ? this.#compiled.get(id) : undefined;
  }

  /** The catalog `id`, asking for it first when this session has not. */
  get(id: string): CatalogEntry {
    let held = this.#held.get(id);
    if (!held) {
      held = { entry: { state: "loading" } };
      this.#held.set(id, held);
      this.#request(id);
    }
    return held.entry;
  }

  /** Asks the backend for the catalog `id` again and takes its answer even when it is the one held. */
  reload(id: string): void {
    const held = this.#held.get(id);
    if (held) held.reply = undefined;
    this.#request(id);
  }

  /**
   * A connection opened: takes its listing and asks for every catalog held again, as the new connection pushes
   * changes only of those it was asked for. The compiles stay until the new answers differ.
   */
  open(listing: readonly CatalogInfoWire[]): void {
    this.#listing = listing;
    for (const id of this.#held.keys()) this.#request(id);
  }

  /** The backend lists other catalogs now. */
  list(listing: readonly CatalogInfoWire[]): void {
    this.#listing = listing;
  }

  /**
   * Takes the backend's answer or push. Whatever differs from the last one replaces the document (a restarted backend
   * counts revisions from the start); one that does not compile, or no document at all, leaves the last compile in
   * place and says why. Reports whether the entry changed.
   */
  receive(
    id: string,
    revision: number,
    document: string | null,
    problem: string | null = null,
  ): boolean {
    const held = this.#held.get(id);
    const last = held?.reply;
    if (
      !held ||
      (last?.revision === revision && last.document === document && last.problem === problem)
    )
      return false;
    held.reply = { revision, document, problem };
    const catalog = held.entry.state === "ready" ? held.entry.catalog : undefined;
    if (document === null) {
      held.entry = { state: "ready", catalog, problem: problem ?? `There is no catalog ${id}` };
      return true;
    }
    const result = compileCatalog(document);
    if (result.ok) this.#compiled.set(id, revision);
    held.entry = result.ok
      ? { state: "ready", catalog: result.catalog }
      : { state: "ready", catalog, problem: describe(result.diagnostics, document) };
    return true;
  }
}
