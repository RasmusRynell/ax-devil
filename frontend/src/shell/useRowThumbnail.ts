import { useMemo } from "react";
import { useCan, useSession } from "../session/session";
import { useHoverPicture } from "./useHoverPicture";
import type { TreeRow, VisibleRow } from "./treeModel";

/** The entry whose first frame a row's hover card shows; none for lanes, playlists and rows that are not Ready. */
export const pictureOf = (row: TreeRow) =>
  row.target && row.info.kind !== "lane" ? row.target.entry : null;

const urlOf = (session: string, entry: string) =>
  `/api/thumbnail?${new URLSearchParams({ session, entry })}`;

/**
 * The hover picture of the workspace rows on screen, keyed by `pictureOf`. A hover ends when its row is no longer
 * one of the `rows`, or when the session or the Files grant changes. Nothing is requested without the Files grant,
 * which the backend checks too.
 */
export function useRowThumbnail(rows: readonly VisibleRow[]) {
  const can = useCan("host");
  const session = useSession().session?.id;
  const urls = useMemo(
    () =>
      new Set(
        can && session
          ? rows.flatMap(({ row }) => pictureOf(row) ?? []).map((entry) => urlOf(session, entry))
          : [],
      ),
    [rows, can, session],
  );
  const { shown, enter, leave } = useHoverPicture(urls);
  return {
    shown,
    enter: (entry: string | null, x: number, y: number) =>
      enter(entry && can && session ? urlOf(session, entry) : null, x, y),
    leave,
  };
}
