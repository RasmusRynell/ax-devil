/** [AI] Module state, not React state: the window is one page, and a command outlives the component that sent it. */
const held = new Map<string, number>();

/**
 * Marks `items` as being placed or closed by this window's own command until the returned function is called, so
 * the placing the window does for every open item that has no viewer leaves them alone and no item gets two.
 */
export function hold(items: readonly string[]): () => void {
  for (const item of items) held.set(item, (held.get(item) ?? 0) + 1);
  return () => {
    for (const item of items) {
      const left = (held.get(item) ?? 1) - 1;
      if (left > 0) held.set(item, left);
      else held.delete(item);
    }
  };
}

/** Whether a command of this window is placing or closing the viewers of `item`. */
export const isHeld = (item: string): boolean => held.has(item);
