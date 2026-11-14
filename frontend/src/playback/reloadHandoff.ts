/** Where a viewer that went away left its clock, for the viewer a reload of its Content mounts in its place. */
export type Handoff = {
  content: string;
  generation: number;
  entry: string;
  frame: number;
  speed: number;
  playing: boolean;
};

const left = new Map<number, Handoff>();

/**
 * Keeps `handoff` for the viewer `viewer` until the commit that mounts its replacement has run. A reload replaces the
 * viewer in one commit, so a record still here after it is for nobody: an item that resolves again later opens as any
 * viewer does.
 */
export function leave(viewer: number, handoff: Handoff): void {
  left.set(viewer, handoff);
  setTimeout(() => {
    if (left.get(viewer) === handoff) left.delete(viewer);
  }, 0);
}

/** What the viewer before this one left, once, when this one shows the same entry of newer Content. */
export function take(
  viewer: number,
  content: string,
  generation: number,
  entry: string,
): Handoff | undefined {
  const handoff = left.get(viewer);
  if (!handoff || handoff.content !== content || handoff.entry !== entry) return undefined;
  if (generation <= handoff.generation) return undefined;
  left.delete(viewer);
  return handoff;
}
