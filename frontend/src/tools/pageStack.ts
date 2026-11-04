/**
 * The pages drilled into from the media tools' root list, as plain values: what the panel shows, what Back returns to
 * and which row of the list the open page came from. The panel and the viewer hold one and replace it with the result
 * of these functions.
 */

/** A page: one object of the lane. Another kind of page is another member of this union. */
export type PageKey = { readonly kind: "object"; readonly entity: string };

/** The root pages the pages are drilled from: the Objects list. */
export type Root = "objects";

/** A page and the root list it was chosen from, which steps to its neighbours; `null` for one pushed from a page. */
export type Entry = { readonly key: PageKey; readonly origin: Root | null };

export type PageStack = {
  readonly entries: readonly Entry[];
  /** The row of the root list the last page was opened from, which stays marked after Back. */
  readonly mark: PageKey | null;
};

/** No page open, nothing marked. */
export const EMPTY: PageStack = { entries: [], mark: null };

/** What the title of a page and the Back button to it say. */
export const titleOf = (key: PageKey): string => key.entity;

export const sameKey = (a: PageKey, b: PageKey): boolean =>
  a.kind === b.kind && a.entity === b.entity;

const top = (stack: PageStack): Entry | undefined => stack.entries.at(-1);

/** The page on top, or `null` while the root shows. */
export const topKey = (stack: PageStack): PageKey | null => top(stack)?.key ?? null;

/** The object whose page is on top, or `null`. */
export function topObject(stack: PageStack): PageKey | null {
  const key = topKey(stack);
  return key?.kind === "object" ? key : null;
}

/** `entries` shown: a page opened from the root list marks its row. */
const shown = (stack: PageStack, entries: readonly Entry[]): PageStack => {
  const last = entries.at(-1);
  return { entries, mark: last?.origin ? last.key : stack.mark };
};

/** Opens `key` on top of what shows; only a page opened from the root list has an origin. */
export function push(stack: PageStack, key: PageKey): PageStack {
  const entry: Entry = { key, origin: stack.entries.length === 0 ? "objects" : null };
  return shown(stack, [...stack.entries, entry]);
}

/**
 * Opens `key`, replacing the page on top when it is of the same kind and keeping the list it was opened from, else
 * pushing it: choosing objects one after another does not grow the history.
 */
export function jump(stack: PageStack, key: PageKey): PageStack {
  const last = top(stack);
  if (last?.key.kind !== key.kind) return push(stack, key);
  return shown(stack, [...stack.entries.slice(0, -1), { key, origin: last.origin }]);
}

/** Replaces the page on top with `key`, a neighbour in the list it was opened from. */
export function step(stack: PageStack, key: PageKey): PageStack {
  const last = top(stack);
  return last ? shown(stack, [...stack.entries.slice(0, -1), { key, origin: last.origin }]) : stack;
}

/** Goes back exactly one level; from the first page, the row it was opened from stays marked. */
export function back(stack: PageStack): PageStack {
  const popped = top(stack);
  if (!popped) return stack;
  const entries = stack.entries.slice(0, -1);
  return { entries, mark: entries.length === 0 && popped.origin ? popped.key : stack.mark };
}

/** The objects either side of the open page's, in the list's current order; `null` where there is none. */
export type Steps = { previous: string | null; next: string | null };

/** The rows a list has loaded: `keys` start at row `offset` of the `matched` the list's order has. */
export type Rows = { keys: readonly string[]; offset: number; matched: number };

/**
 * The key `delta` rows from `entity` in the list's current order; `null` past either end, when `entity` is not in the
 * order, or when that row is not loaded yet.
 */
export function neighbour(
  { keys, offset, matched }: Rows,
  entity: string,
  delta: number,
): string | null {
  const at = keys.indexOf(entity);
  const row = offset + at + delta;
  return at < 0 || row < 0 || row >= matched ? null : (keys[at + delta] ?? null);
}
