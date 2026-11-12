/** How long a read waits before retry or terminal failure, independent of display refresh. */
export const INSPECTION_RETRY_MS = 2000;
/** Retries after the initial first-inspection/search request; continuations are not replayed. */
export const INSPECTION_RETRIES = 3;
/** Includes the final response window after the last retry, or one continuation's response window. */
export const INSPECTION_WAIT_MS = INSPECTION_RETRY_MS * (INSPECTION_RETRIES + 1);
/** Schedules one response window and returns cancellation. */
export const afterInspection = (run: () => void, ms: number): (() => void) => {
  const timer = setTimeout(run, ms);
  return () => clearTimeout(timer);
};
