import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { EntryInfo, PlaylistSpanInfo } from "../generated/api";
import { Icon } from "../kit/Icon";
import { IconButton } from "../kit/IconButton";
import { Menu } from "../kit/Menu";
import { Popover } from "../kit/Popover";
import type { PreparedEntry } from "../playback/nextEntry";
import { useFollowing, useView } from "../session/session";
import type { ViewerProps } from "../viewers";
import { NO_ACTIONS, ViewerActionsContext, useViewerActions, type ViewerActions } from "./actions";
import { EntryViewer } from "./EntryViewer";
import type { Hidden } from "./hidden";
import { shownEntry } from "./shownEntry";
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
 * Shows a playlist: its entries one at a time in a video viewer, with buttons and a menu to move between them, none
 * of which a single entry needs. An item's playlists are separate: stepping, the count and the menu stay within the
 * one holding the entry on screen, whose label the header shows. Hidden entries are skipped when stepping, and an
 * entry on screen that gets hidden gives way to the next one, else the previous one. When every entry is hidden the
 * one on screen stays. The entry on screen is the window's view of this item, so it is the presenter's for a
 * following window and survives a reload. An entry chosen starts over, as the view keeps only the clock of the entry
 * on screen.
 */
export function PlaylistViewer({ item, viewer, content }: ViewerProps<"Playlist">) {
  const { entries, playlists } = content;
  const hidden = useHidden();
  const [viewers, setViewers] = useView("viewers");
  const following = useFollowing();
  // What the entry's own viewer offers (play, step, speed) stays reachable next to the playlist's commands.
  const [inner, setInner] = useState<ViewerActions>(NO_ACTIONS);

  // The backend keeps the view naming an entry; until it has, `shownEntry` makes the same choice, so the header menu
  // acts on this entry too.
  const shown = shownEntry(entries, hidden, viewers[viewer]?.entry);
  const found = shown ? entries.indexOf(shown) : -1;
  const { label, start, end } = playlistAt(playlists, Math.max(found, 0));
  const within = entries.slice(start, end);
  const index = Math.max(found - start, 0);
  const current = within[index] as EntryInfo | undefined;
  const currentHidden = current !== undefined && hidden.entry(current.id);
  const previous = adjacentEntry(within, hidden, index, -1);
  const next = adjacentEntry(within, hidden, index, 1);

  // Another entry starts at its own beginning, so the view keeps the entry and no clock.
  const choose = useCallback(
    (id: string) =>
      setViewers((now) => {
        const was = now[viewer];
        if (!was || was.entry === id) return now;
        return { ...now, [viewer]: { item: was.item, pinned: was.pinned, entry: id } };
      }),
    [setViewers, viewer],
  );
  const [previousId, nextId] = [previous?.id, next?.id];
  // What the backend is asked to prepare: the next entry's lanes the user has not hidden, and nothing when it has
  // none left to show, as the reference withdraws then. An entry without overlays has its one plain lane. The lanes
  // are kept as one text so that the same lanes are the same value, whatever their labels hold.
  const shownLanes = next?.lanes.filter((lane) => !hidden.lane(next.id, lane));
  const nextLanes = shownLanes?.length ? JSON.stringify(shownLanes) : undefined;
  const upNext = useMemo<PreparedEntry | null>(
    () =>
      nextId !== undefined && nextLanes !== undefined
        ? { entry: nextId, lanes: JSON.parse(nextLanes) as string[] }
        : null,
    [nextId, nextLanes],
  );
  // An entry hidden while it is on screen is left for a neighbour; one the view named hidden is shown, as when the
  // tree opened it. A window that follows takes the presenter's choice.
  const seen = useRef<{ id: string; hidden: boolean }>(undefined);
  useEffect(() => {
    const before = seen.current;
    seen.current = current && { id: current.id, hidden: currentHidden };
    if (!current || !currentHidden || following || before?.id !== current.id || before.hidden)
      return;
    const replacement = nextId ?? previousId;
    if (replacement) choose(replacement);
  }, [current, currentHidden, following, nextId, previousId, choose]);
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
        This playlist has no entries
      </p>
    );
  const listed = within.filter((entry) => entry.id === current.id || !hidden.entry(entry.id));
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {(within.length > 1 || playlists.length > 1) && (
        <div className="flex h-row shrink-0 items-center border-b border-line bg-panel">
          {within.length > 1 && (
            <>
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
            </>
          )}
          <span className="min-w-0 flex-1 truncate px-m text-muted">{label}</span>
        </div>
      )}
      <ViewerActionsContext.Provider value={setInner}>
        <EntryViewer item={item} viewer={viewer} content={content} entry={current} next={upNext} />
      </ViewerActionsContext.Provider>
    </div>
  );
}
