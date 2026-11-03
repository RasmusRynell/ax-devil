import type { EntryInfo, HiddenTarget, ItemState, RowInfo } from "../generated/api";

/** A playlist item whose playlists the backend lists as separate rows when there are several. */
export type GroupedItem = ItemState & { groups?: readonly { name: string; entries: number }[] };

export const hides = (hidden: readonly HiddenTarget[], entry: string, lane?: string) =>
  hidden.some((target) => target.entry === entry && target.lane === lane);

/** The sidebar tree for these items, as the backend flattens it. */
export function rowsOf(items: readonly ItemState[], hidden: readonly HiddenTarget[]): RowInfo[] {
  const ready = { status: "Ready" } as const;
  const lanes = (entry: EntryInfo): RowInfo[] =>
    entry.lanes.map((lane) => ({
      key: `${entry.id}\u001f${lane}`,
      kind: "lane",
      label: lane,
      status: ready,
      hidden: hides(hidden, entry.id, lane),
      entry: entry.id,
      warnings: [],
    }));
  return items.flatMap((item): RowInfo[] => {
    const { resolution } = item;
    const base = {
      key: item.id,
      kind: "item" as const,
      label: item.label,
      hidden: false,
      warnings: [],
    };
    if (resolution.status === "Pending")
      return [{ ...base, status: { status: "Pending" }, entry: null }];
    if (resolution.status === "Failed")
      return [{ ...base, status: { ...resolution, status: "Unavailable" }, entry: null }];
    const { content } = resolution;
    if (content.kind === "SeekableVideo")
      return [
        {
          ...base,
          status: ready,
          hidden: hides(hidden, content.entry.id),
          entry: content.entry.id,
        },
        ...lanes(content.entry),
      ];
    const entryRows = (entry: EntryInfo): RowInfo[] => [
      {
        key: entry.id,
        kind: "entry",
        label: entry.label,
        status: ready,
        hidden: hides(hidden, entry.id),
        entry: entry.id,
        warnings: [],
      },
      ...lanes(entry),
    ];
    const groups = (item as GroupedItem).groups ?? [];
    let next = 0;
    return [
      { ...base, status: ready, entry: content.entries[0]?.id ?? null },
      ...(groups.length > 1
        ? groups.flatMap(({ name, entries }): RowInfo[] => {
            const own = content.entries.slice(next, (next += entries));
            return [
              {
                key: `${item.id}/${name}`,
                kind: "playlist",
                label: name,
                status: ready,
                hidden: false,
                entry: own[0]?.id ?? null,
                warnings: [],
              },
              ...own.flatMap(entryRows),
            ];
          })
        : content.entries.flatMap(entryRows)),
    ];
  });
}
