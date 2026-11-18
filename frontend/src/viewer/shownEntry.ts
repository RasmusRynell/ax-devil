import type { ContentInfo, EntryInfo } from "../generated/api";
import type { Hidden } from "./hidden";

/** The entries of an item's Content in order: a video's one, a playlist's, none for a live stream. */
export function entriesOf(content: ContentInfo): readonly EntryInfo[] {
  switch (content.kind) {
    case "SeekableVideo":
      return [content.entry];
    case "Playlist":
      return content.entries;
    case "LiveVideo":
      return [];
  }
}

/**
 * The entry a viewer of an item shows when its view names `requested`: that entry while the item has it (hidden or
 * not, as when the tree opened it), else the first entry of the whole item the user has not hidden, else the first.
 * The backend decides the same (`shown_entry` in the sessions crate) when it settles a view, so the viewer, its
 * header menu and the backend all act on one entry, also while the view names none yet.
 */
export function shownEntry(
  entries: readonly EntryInfo[],
  hidden: Hidden,
  requested: string | undefined,
): EntryInfo | undefined {
  return (
    entries.find((entry) => entry.id === requested) ??
    entries.find((entry) => !hidden.entry(entry.id)) ??
    entries[0]
  );
}
