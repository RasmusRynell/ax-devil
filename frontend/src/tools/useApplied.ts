import { useCallback, useEffect, useState } from "react";

/** How long typing must pause before the text is applied. */
export const APPLY_MS = 150;

/**
 * The part of `text` to search the frame's objects by: it follows `text` once typing has paused for `APPLY_MS`, and
 * `apply` makes it follow at once, cancelling the wait.
 */
export function useApplied(text: string): [applied: string, apply: (text: string) => void] {
  const [applied, setApplied] = useState(text);
  useEffect(() => {
    if (text === applied) return;
    const timer = setTimeout(() => setApplied(text), APPLY_MS);
    return () => clearTimeout(timer);
  }, [text, applied]);
  return [applied, useCallback((now: string) => setApplied(now), [])];
}
