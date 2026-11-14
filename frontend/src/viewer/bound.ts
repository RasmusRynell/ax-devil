import { useSyncExternalStore } from "react";
import type { PlaybackStream } from "../playback/stream";

/** A viewer that plays an entry and can be driven. */
export interface BoundViewer {
  viewer: number;
  item: string;
  entry: string;
  stream: PlaybackStream;
}

const bound = new Map<number, BoundViewer>();
let held: ReadonlyMap<number, BoundViewer> = new Map();
const listeners = new Set<() => void>();

/** Offers `target` to the sidebar views while the viewer plays; the returned function takes it back. */
export function bindViewer(target: BoundViewer): () => void {
  const change = (next: () => void) => {
    next();
    held = new Map(bound);
    listeners.forEach((listener) => listener());
  };
  change(() => bound.set(target.viewer, target));
  return () => change(() => bound.get(target.viewer) === target && bound.delete(target.viewer));
}

/** The viewers offered now, the one in front first when `front` is given. */
export function boundViewers(front?: number): BoundViewer[] {
  return [...held.values()].sort((a, b) => Number(b.viewer === front) - Number(a.viewer === front));
}

/** The viewer `viewer` offered, once it plays an entry. */
export function useBoundViewer(viewer: number | undefined): BoundViewer | undefined {
  const all = useSyncExternalStore(
    (listener) => (listeners.add(listener), () => void listeners.delete(listener)),
    () => held,
  );
  return viewer === undefined ? undefined : all.get(viewer);
}
