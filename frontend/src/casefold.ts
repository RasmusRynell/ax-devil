/**
 * Text with case folded away as Python's `str.casefold` does (Cherokee aside: Python folds it to capitals, this to small
 * letters, so the same text matches but sorts differently among other scripts): each code point alone, so `ς` equals `σ` whatever surrounds it. `ß` and ligatures expand
 * through the upper case; `ẞ` is `ss` and the dotless `ı` stays.
 */
export const casefold = (text: string): string =>
  Array.from(text, (c) => (c === "ẞ" ? "ss" : c === "ı" ? c : c.toUpperCase().toLowerCase())).join(
    "",
  );

/** Orders two texts by code point, which UTF-16 order does not for characters beyond the first 65,536. */
export function compareCodePoints(a: string, b: string): number {
  const [x, y] = [
    Array.from(a, (c) => c.codePointAt(0) ?? 0),
    Array.from(b, (c) => c.codePointAt(0) ?? 0),
  ];
  for (let at = 0; at < Math.min(x.length, y.length); at++)
    if (x[at] !== y[at]) return x[at] < y[at] ? -1 : 1;
  return x.length - y.length;
}

/** Orders two names as Python's `sorted(key=str.casefold)` does. */
export const compareNames = (a: string, b: string): number =>
  compareCodePoints(casefold(a), casefold(b));
