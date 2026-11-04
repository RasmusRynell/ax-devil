import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { EntryInfo, PlaylistSpanInfo } from "../generated/api";
import { Icon } from "../kit/Icon";
import { IconButton } from "../kit/IconButton";
import { Menu } from "../kit/Menu";
import { Popover } from "../kit/Popover";
import type { EntryMemory } from "../playback/VideoViewer";
import { useView } from "../session/session";
import type { ViewerProps } from "../viewers";
import { NO_ACTIONS, ViewerActionsContext, useViewerActions, type ViewerActions } from "./actions";
import { EntryViewer } from "./EntryViewer";
import type { Hidden } from "./hidden";
import { useHidden } from "./useViewers";

/**
 * The entry `step` places from `from` (negative is back) that the user has not hidden, or `undefined` when there is
 * none that way. Entries do not wrap around.
 */
export function adjacentEntry(
  entries: readonly EntryInfo[],
  hidden: Hidden,
  from: number,
  step: 1 | -1,
): EntryInfo | undefined {
  for (let at = from + step; at >= 0 && at < entries.length; at += step)
    if (!hidden.entry(entries[at].id)) return entries[at];
  return undefined;
}

/** The playlist holding the entry at `at`: its label and the run `[start, end)` of the item's entries it covers. */
function playlistAt(playlists: readonly PlaylistSpanInfo[], at: number) {
  let start = 0;
  for (const { label, entries } of playlists.slice(0, -1)) {
    if (at < start + entries) return { label, start, end: start + entries };
    start += entries;
  }
  const last = playlists[playlists.length - 1];
  return { label: last.label, start, end: start + last.entries };
}

/**
 * Shows a playlist: its entries one at a time in a video viewer, with buttons and a menu to move between them.
 * An item's playlists are separate: stepping, the count and the menu stay within the one holding the entry on
 * screen, whose label the header shows. Hidden entries are skipped when stepping. The entry on screen is the window's view of this item, so it is the
 * presenter's for a following window and survives a reload. A position is kept for each entry that was left, until
 * the viewer closes or the entry leaves the playlist, so returning to an entry reopens it there; the view keeps only
 * the clock of the entry on screen.
 */
export function PlaylistViewer({ item, content }: ViewerProps<"Playlist">) {
  const { entries, playlists } = content;
  const hidden = useHidden();
  const [viewers, setViewers] = useView("viewers");
  // What the entry's own viewer offers (play, step, speed) stays reachable next to the playlist's commands.
  const memory = useRef<EntryMemory>(new Map());
  useEffect(() => {
    const listed = new Set(entries.map((entry) => entry.id));
    for (const id of memory.current.keys()) if (!listed.has(id)) memory.current.delete(id);
  }, [entries]);
  const [inner, setInner] = useState<ViewerActions>(NO_ACTIONS);

  // The backend keeps the view naming an entry; until it has, the first one that is not hidden is its choice too.
  const chosen = viewers[item]?.entry;
  const found = entries.findIndex((entry) => entry.id === chosen);
  const { label, start, end } = playlistAt(playlists, Math.max(found, 0));
  const within = entries.slice(start, end);
  const index = found >= 0 ? found - start : within.findIndex((entry) => !hidden.entry(entry.id));
  const current = within[index] as EntryInfo | undefined;
  const previous = adjacentEntry(within, hidden, index, -1);
  const next = adjacentEntry(within, hidden, index, 1);

  // Another entry starts at its own beginning, so the view keeps the entry and no clock.
  const choose = useCallback(
    (id: string) =>
      setViewers((now) => (now[item]?.entry === id ? now : { ...now, [item]: { entry: id } })),
    [setViewers, item],
  );
  const [previousId, nextId] = [previous?.id, next?.id];
  const actions = useMemo<ViewerActions>(
    () => ({
      ...inner,
      ...(previousId && { "previous-entry": () => choose(previousId) }),
      ...(nextId && { "next-entry": () => choose(nextId) }),
    }),
    [inner, previousId, nextId, choose],
  );
  useViewerActions(actions);

  if (!current)
    return (
      <p role="status" className="m-auto p-xl text-muted">
        {entries.length === 0 ? "This playlist has no entries" : "All entries are hidden"}
      </p>
    );
  const listed = within.filter((entry) => entry.id === current.id || !hidden.entry(entry.id));
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-row shrink-0 items-center border-b border-line bg-panel">
        <IconButton
          icon="chevron-left"
          label="Previous entry"
          data-cursor-id="entry:previous"
          disabled={!previous}
          onClick={() => previous && choose(previous.id)}
        />
        <Popover
          label={`Entry: ${current.label}`}
          className="px-m"
          trigger={
            <>
              <span className="truncate">{current.label}</span>
              <Icon name="chevron-down" />
            </>
          }
        >
          {(close) => (
            <Menu
              entries={listed.map((entry) => ({
                kind: "check" as const,
                id: entry.id,
                label: entry.label,
              }))}
              checked={new Set([current.id])}
              onSelect={({ id }) => {
                close();
                choose(id);
              }}
            />
          )}
        </Popover>
        <IconButton
          icon="chevron-right"
          label="Next entry"
          data-cursor-id="entry:next"
          disabled={!next}
          onClick={() => next && choose(next.id)}
        />
        <output aria-label="Entry" className="px-m text-muted tabular-nums">
          {index + 1} / {within.length}
        </output>
        <span className="min-w-0 flex-1 truncate px-m text-muted">{label}</span>
      </div>
      <ViewerActionsContext.Provider value={setInner}>
        <EntryViewer item={item} content={content} entry={current} memory={memory.current} />
      </ViewerActionsContext.Provider>
    </div>
  );
}
