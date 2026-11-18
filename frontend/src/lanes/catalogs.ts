/** The catalogs a stream has asked the backend for, compiled once per id and shared by every lane that uses them. */

import { readFreshCatalog } from "./freshCatalog";
import { compileCatalog, locate, type CompiledCatalog, type Diagnostic } from "../present";
import type { CatalogInfoWire } from "../stream/protocol";

/** How many diagnostics of a failed compile the lane's notice lists. */
const SHOWN_DIAGNOSTICS = 5;

/** What a lane may draw with. `catalog` is the last compile that worked; `problem` says why a newer document failed. */
type CatalogEntry =
  | { state: "loading" }
  | { state: "ready"; catalog?: CompiledCatalog; problem?: string };

/** What the backend last said of a catalog: revisions restart with the backend, so the text counts too. */
type Reply = { revision: number; document: string | null; problem: string | null };

type Held = {
  reply?: Reply;
  epoch?: number;
  /** Whether the latest reply was delivered by the playback feed, which retains it for export. */
  delivered?: boolean;
  /** Last document revision, retained across an availability withdrawal. */
  revision?: number;
  /** Counts ordered feed availability transitions, independently of document revisions. */
  availability: number;
  entry: CatalogEntry;
};

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
  #epoch = 0;
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

  /** The revision of the document the catalog `id` is drawn from; absent until one compiled. */
  compiledRevision(id: string): number | undefined {
    return this.#compiled.get(id);
  }

  /** The catalog `id`, asking for it first when this stream has not. */
  get(id: string): CatalogEntry {
    let held = this.#held.get(id);
    if (!held) {
      held = { entry: { state: "loading" }, availability: 0 };
      this.#held.set(id, held);
      this.#request(id);
    }
    return held.entry;
  }

  /** Asks for the catalog again without forgetting the revision already received. */
  reload(id: string): void {
    this.#request(id);
  }

  /**
   * A connection opened: takes its listing and asks for every catalog held again, as the new connection pushes
   * changes only of those it was asked for. The compiles stay until the new answers differ.
   */
  open(listing: readonly CatalogInfoWire[]): void {
    this.#epoch++;
    this.#listing = listing;
    for (const id of this.#held.keys()) this.#request(id);
  }

  /** Rereads for an action, reconciles with socket pushes, and accepts only the current valid document. */
  async validate(id: string, current: () => boolean): Promise<boolean> {
    const epoch = this.#epoch;
    this.get(id);
    const availability = this.#held.get(id)?.availability;
    const fresh = await readFreshCatalog(id);
    if (!current() || epoch !== this.#epoch) return false;
    // A reply started before a withdrawal cannot restore availability; only the ordered feed can do that.
    if (availability === this.#held.get(id)?.availability)
      this.#accept(id, fresh.revision, fresh.document, null, false);
    const held = this.get(id);
    if (held.state !== "ready" || !held.catalog || held.problem)
      throw new Error(held.state === "ready" ? held.problem : "The catalog is loading");
    return true;
  }

  /** The backend lists other catalogs now. */
  list(listing: readonly CatalogInfoWire[]): void {
    this.#listing = listing;
  }

  /**
   * Takes answers and pushes in revision order within a connection; reopening permits a restarted backend's lower
   * revisions. A document that does not compile, or no document at all, leaves the last compile in
   * place and says why. Reports whether the entry changed.
   */
  receive(
    id: string,
    revision: number,
    document: string | null,
    problem: string | null = null,
  ): boolean {
    return this.#accept(id, revision, document, problem, true);
  }

  #accept(
    id: string,
    revision: number,
    document: string | null,
    problem: string | null,
    delivered: boolean,
  ): boolean {
    const held = this.#held.get(id);
    const last = held?.reply;
    if (!held) return false;
    // Revision zero is an availability withdrawal, not an older document. Socket messages are ordered.
    const withdrawn = revision === 0 && document === null && problem === null;
    if (held.epoch === this.#epoch) {
      if (!withdrawn && revision < (held.revision ?? 0)) return false;
    } else held.revision = undefined;
    held.epoch = this.#epoch;
    if (!withdrawn) held.revision = revision;
    if (
      last?.revision === revision &&
      last.document === document &&
      last.problem === problem &&
      (!delivered || held.delivered)
    )
      return false;
    if ((last?.document === null) !== (document === null)) held.availability++;
    held.reply = { revision, document, problem };
    held.delivered = delivered;
    const catalog = held.entry.state === "ready" ? held.entry.catalog : undefined;
    if (document === null) {
      held.entry = { state: "ready", catalog, problem: problem ?? `There is no catalog ${id}` };
      return true;
    }
    const result = compileCatalog(document);
    // Only feed delivery proves the export owner retained this document. HTTP still validates immediately.
    if (result.ok && delivered) this.#compiled.set(id, revision);
    held.entry = result.ok
      ? { state: "ready", catalog: delivered ? result.catalog : catalog }
      : { state: "ready", catalog, problem: describe(result.diagnostics, document) };
    return true;
  }
}
