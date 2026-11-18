import { useEffect, useSyncExternalStore } from "react";

/** A request to put the keyboard on one lane of the viewer that shows `entry`. */
interface Request {
  viewer: number;
  entry: string;
  lane: string;
}

// The one request there is: it belongs to this window's keyboard, which no other window or follower shares.
let pending: Request | null = null;
const listeners = new Set<() => void>();

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => void listeners.delete(listener);
};

function set(next: Request | null) {
  if (pending === next) return;
  pending = next;
  listeners.forEach((listener) => listener());
}

/**
 * Asks for the lane named `lane` of `entry`, shown by `viewer`, to take the keyboard once it can. The request replaces
 * the one before it; without a lane it only cancels that one.
 */
export function focusLane(viewer: number, entry: string | undefined, lane: string | undefined) {
  set(entry !== undefined && lane !== undefined ? { viewer, entry, lane } : null);
}

/** Drops the request for `viewer`, as a pane does when it stops being the active one before the lane could take it. */
export function dropLaneFocus(viewer: number) {
  if (pending?.viewer === viewer) set(null);
}

/**
 * Carries out the request for `viewer` once it shows `entry` and has the lane among its `shown` ones (of `labels`, by
 * index), by calling `apply` with its index; then the request is spent. It never seeks or restarts. A request for
 * another entry is dropped, and one for a lane not shown yet waits. Only an `active` pane takes the keyboard.
 */
export function useLaneFocus(
  viewer: number,
  entry: string,
  labels: readonly string[],
  shown: readonly number[],
  active: boolean,
  apply: (lane: number) => void,
) {
  const request = useSyncExternalStore(subscribe, () => pending);
  useEffect(() => {
    if (!request || request.viewer !== viewer) return;
    if (request.entry !== entry) return set(null);
    if (!active) return;
    const lane = labels.findIndex((label) => label === request.lane);
    if (lane < 0 || !shown.includes(lane)) return;
    set(null);
    apply(lane);
  }, [request, viewer, entry, labels, shown, active, apply]);
}
