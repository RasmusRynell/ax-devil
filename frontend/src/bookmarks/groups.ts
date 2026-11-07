import type { BookmarkGroupInfo, BookmarkInfo, BookmarkKindWire } from "../generated/api";
import { casefold } from "../casefold";
import { timecode } from "../playback/timecode";

/** The kinds in the order the filter and the kind menu list them, with their names. */
export const KINDS: readonly (readonly [BookmarkKindWire, string])[] = [
  ["note", "Note"],
  ["issue", "Issue"],
  ["example", "Example"],
  ["question", "Question"],
];

const NAMES = new Map(KINDS);

/** The kind's name. */
export const kindName = (kind: BookmarkKindWire): string => NAMES.get(kind) ?? kind;

/** What a bookmark is called: its text, else its frame. */
export const labelOf = ({ note, frame }: BookmarkInfo): string => note || `Frame ${frame}`;

/** What a bookmark's row says under its label: its time, frame and kind. */
export const subtitleOf = ({ time_us, frame, kind }: BookmarkInfo): string =>
  `${timecode(time_us)} · frame ${frame} · ${kindName(kind)}`;

/** A group's title: its item, then its entry for a playlist entry. */
export const titleOf = ({ item_name, entry }: BookmarkGroupInfo): string =>
  entry ? `${item_name} › ${entry}` : item_name;

/** How many bookmarks the groups hold. */
export const countOf = (groups: readonly BookmarkGroupInfo[]): number =>
  groups.reduce((total, group) => total + group.bookmarks.length, 0);

/**
 * The groups with only the bookmarks of `kind` (any when `null`) whose label or group title holds `query`, ignoring
 * case and surrounding spaces. A group left without bookmarks drops out, except that a reviewed one that had none
 * stays while no kind is chosen and the query matches its title.
 */
export function filterGroups(
  groups: readonly BookmarkGroupInfo[],
  query: string,
  kind: BookmarkKindWire | null,
): BookmarkGroupInfo[] {
  const needle = casefold(query.trim());
  return groups.flatMap((group) => {
    const titled = casefold(titleOf(group)).includes(needle);
    const bookmarks = group.bookmarks.filter(
      (bookmark) =>
        (kind === null || bookmark.kind === kind) &&
        (titled || casefold(labelOf(bookmark)).includes(needle)),
    );
    const kept = bookmarks.length > 0 || (group.bookmarks.length === 0 && kind === null && titled);
    return kept ? [{ ...group, bookmarks }] : [];
  });
}
