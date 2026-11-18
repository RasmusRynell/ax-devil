/** What a lane draws with: its chosen catalog, or the best one it has when the chosen one cannot be used. */

import type { CompiledCatalog } from "../present";
import type { Catalogs } from "./catalogs";

export type LaneCatalog = {
  /** The catalog to draw with; absent only while the built-in default is still loading, or if it never compiled. */
  catalog?: CompiledCatalog;
  /** Whether the lane should wait for a catalog that is on its way. */
  pending: boolean;
  /** Why the chosen catalog is not what the lane draws with, or only partly: its newest document failed. */
  problem?: string;
};

/**
 * Chooses the catalog of each lane so that a catalog which fails never leaves a lane drawing nothing: the chosen one
 * when it compiled, else the one the lane drew last, else the built-in default.
 */
export class LaneCatalogs {
  readonly #catalogs: Catalogs;
  readonly #fallback: () => string;
  readonly #actions = new Map<
    number,
    { chosen: string; catalog?: CompiledCatalog; problem: string }
  >();
  readonly #drawn = new Map<number, CompiledCatalog>();

  /** `fallback` gives the id of the built-in default catalog. */
  constructor(catalogs: Catalogs, fallback: () => string) {
    this.#catalogs = catalogs;
    this.#fallback = fallback;
  }

  /** What `lane` draws with when it chose `chosen`; asks for catalogs not yet asked for. */
  resolve(lane: number, chosen: string): LaneCatalog {
    const entry = this.#catalogs.get(chosen);
    const problem = entry.state === "ready" ? entry.problem : undefined;
    if (entry.state === "ready" && entry.catalog) {
      this.#drawn.set(lane, entry.catalog);
      return { catalog: entry.catalog, pending: false, problem };
    }
    const previous = this.#drawn.get(lane);
    if (previous) return { catalog: previous, pending: false, problem };
    const fallback = this.#catalogs.get(this.#fallback());
    if (fallback.state === "loading") return { pending: true, problem };
    return { catalog: fallback.catalog, pending: false, problem };
  }

  /** Records an Apply refusal only when the catalog does not already own its cause. */
  refuseApply(lane: number, chosen: string, problem: string): void {
    const entry = this.#catalogs.get(chosen);
    if (entry.state === "ready" && entry.problem) {
      this.#actions.delete(lane);
      return;
    }
    this.#actions.set(lane, {
      chosen,
      catalog: entry.state === "ready" ? entry.catalog : undefined,
      problem,
    });
  }

  /** An action-only failure lasts until selection changes, a successful compile, or dismissal. */
  actionProblem(lane: number, chosen: string): string | undefined {
    const action = this.#actions.get(lane);
    if (!action) return undefined;
    const entry = this.#catalogs.get(chosen);
    if (
      chosen !== action.chosen ||
      (entry.state === "ready" && (entry.problem || entry.catalog !== action.catalog))
    ) {
      this.#actions.delete(lane);
      return undefined;
    }
    return action.problem;
  }

  /** Dismisses the lane's Apply refusal without changing its catalog or backend problem. */
  dismissAction(lane: number): void {
    this.#actions.delete(lane);
  }

  /** A viewer was replaced or closed: no old action refusal belongs to its lanes. */
  clearActions(): void {
    this.#actions.clear();
  }

  /** Lets go of what `lane` drew, as when the lane is not the same lane any more. */
  forget(lane: number): void {
    this.#drawn.delete(lane);
    this.#actions.delete(lane);
  }
}
