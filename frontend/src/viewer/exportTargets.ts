import { useSyncExternalStore } from "react";

/** The export a ready viewer offers, for the entry of the item it shows. */
type Target = { viewer: number; item: string; entry: string; run: () => void };

const targets: Target[] = [];
const listeners = new Set<() => void>();
/** Changes whenever a viewer registers or leaves, which is what `useExportTargets` follows. */
let version = 0;

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => void listeners.delete(listener);
};

/** Offers `run` as the export of the viewer `viewer` showing `entry` of `item`; the returned function takes it back. */
export function registerExport(
  viewer: number,
  item: string,
  entry: string,
  run: () => void,
): () => void {
  const target = { viewer, item, entry, run };
  targets.push(target);
  version += 1;
  listeners.forEach((listener) => listener());
  return () => {
    targets.splice(targets.indexOf(target), 1);
    version += 1;
    listeners.forEach((listener) => listener());
  };
}

/** The export of a viewer showing `entry` of `item` (any entry when undefined); undefined when none is open. */
export function exportOf(item: string, entry?: string): (() => void) | undefined {
  return targets.find((t) => t.item === item && (entry === undefined || t.entry === entry))?.run;
}

/** The entry and export of the viewer `viewer`, once it is ready: a header's own, whatever else shows the item. */
export function shownBy(viewer: number): { entry: string; run: () => void } | undefined {
  return targets.find((t) => t.viewer === viewer);
}

/** Re-renders the caller whenever a viewer starts or stops offering an export, so open menus follow. */
export function useExportTargets(): number {
  return useSyncExternalStore(subscribe, () => version);
}
