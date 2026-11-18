import type { CursorPoint } from "../generated/api";

/** The other participants' cursors. Held outside React state so a moving cursor never re-renders the shell. */
export class Cursors {
  #current: ReadonlyMap<number, CursorPoint> = new Map();
  readonly #listeners = new Set<() => void>();

  subscribe = (listener: () => void) => {
    this.#listeners.add(listener);
    return () => void this.#listeners.delete(listener);
  };
  snapshot = () => this.#current;

  /** Moves a participant's cursor, or removes it. */
  set(participant: number, at: CursorPoint | null) {
    const next = new Map(this.#current);
    if (at) next.set(participant, at);
    else next.delete(participant);
    this.#replace(next);
  }

  /** Drops the cursors of everyone not in `participants`. */
  keepOnly(participants: readonly number[]) {
    const kept = [...this.#current].filter(([id]) => participants.includes(id));
    if (kept.length !== this.#current.size) this.#replace(new Map(kept));
  }

  #replace(next: ReadonlyMap<number, CursorPoint>) {
    this.#current = next;
    this.#listeners.forEach((listener) => listener());
  }
}
