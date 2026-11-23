import type { TreeRow } from "./treeModel";

/**
 * The URL of the picture a row's hover card shows: the first frame of its entry, or a live stream's own picture (the
 * backend answers by item); none for lanes, playlists and rows that are not Ready, and while the seat has no Files
 * grant (`session` is null), which the backend checks too.
 */
export function pictureUrl(
  { info, item, itemKind }: TreeRow,
  session: string | null,
): string | undefined {
  const entry = itemKind === "live" ? item : info.entry;
  if (!session || !entry || info.status.status !== "Ready") return undefined;
  if (info.kind !== "item" && info.kind !== "entry") return undefined;
  return `/api/thumbnail?${new URLSearchParams({ session, entry })}`;
}
