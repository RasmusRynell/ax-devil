/** `now` with `key` added or, when `on` is false, removed. */
export function withKey<T>(now: ReadonlySet<T>, key: T, on: boolean): Set<T> {
  const next = new Set(now);
  if (on) next.add(key);
  else next.delete(key);
  return next;
}
