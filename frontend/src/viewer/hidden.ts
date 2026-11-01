import type { RowInfo } from "../generated/api";

/** What the user hid in the workspace tree: whole entries and single lanes. */
export type Hidden = {
  entry(id: string): boolean;
  lane(entry: string, label: string): boolean;
};

/** Nothing hidden. */
export const NOTHING_HIDDEN: Hidden = { entry: () => false, lane: () => false };

const laneKey = (entry: string, label: string): string => `${entry}\u001f${label}`;

/** What the workspace's rows say is hidden. A video item's row stands for its one entry. */
export function hiddenOf(rows: readonly RowInfo[]): Hidden {
  const entries = new Set<string>();
  const lanes = new Set<string>();
  for (const row of rows) {
    if (!row.hidden || row.entry === null) continue;
    if (row.kind === "lane") lanes.add(laneKey(row.entry, row.label));
    else entries.add(row.entry);
  }
  return {
    entry: (id) => entries.has(id),
    lane: (entry, label) => lanes.has(laneKey(entry, label)),
  };
}
