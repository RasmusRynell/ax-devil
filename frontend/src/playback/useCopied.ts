import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/** How long the check stays on the copy button of the row last copied. */
const COPIED_MS = 1500;

/** The row last copied, of which list, and what it put on the clipboard. */
interface Mark {
  scope: string;
  id: string;
  text: string;
}

/** What `useCopied` gives: `copy` a row's text, and ask whether a row `is` the one marked. */
export type Copied = ReturnType<typeof useCopied>;

/**
 * The one "last copied" mark of a list. `copy` puts text on the clipboard and, only once the browser accepts it, marks
 * the row `id` for 1500 ms; copying again, even the same row, restarts that time, and another row takes the mark. A
 * refused or missing clipboard marks nothing. A mark ends for good as soon as `textOf` (what a row's text is now)
 * no longer gives the text copied for its row, and when `scope`, which says what the rows belong to such as the object shown, changes. Answers that come after a newer
 * copy, after the scope changed or after the list is gone are ignored.
 */
export function useCopied(scope: string, textOf: (id: string) => string | undefined) {
  const [mark, setMark] = useState<Mark | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const latest = useRef(0);
  useEffect(
    () => () => {
      latest.current++;
      clearTimeout(timer.current);
      setMark(null);
    },
    [scope],
  );

  const copy = useCallback(
    (id: string, text: string) => {
      const copy = ++latest.current;
      const write = async () => navigator.clipboard.writeText(text);
      void write().then(
        () => {
          if (copy !== latest.current) return;
          clearTimeout(timer.current);
          setMark({ scope, id, text });
          timer.current = setTimeout(() => setMark(null), COPIED_MS);
        },
        () => {},
      );
    },
    [scope],
  );

  // A mark ends for good when its row's text is no longer what was copied, or the row is gone: coming back to the
  // same text later is another value. Before paint, so the check never shows on a changed row.
  useLayoutEffect(() => {
    if (mark === null || textOf(mark.id) === mark.text) return;
    latest.current++;
    clearTimeout(timer.current);
    setMark(null);
  });

  /** Whether row `id` shows the check. */
  const is = (id: string): boolean => mark?.scope === scope && mark.id === id;
  return { copy, is };
}
