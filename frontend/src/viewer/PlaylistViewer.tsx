import { useEffect, useMemo, useRef, useState } from "react";
import type { EntryInfo } from "../generated/api";
import { Icon } from "../kit/Icon";
import { IconButton } from "../kit/IconButton";
import { Menu } from "../kit/Menu";
import { Popover } from "../kit/Popover";
import { VideoViewer } from "../playback/VideoViewer";
import type { ViewerProps } from "../viewers";
import { useWorkspace } from "../workspace/WorkspaceProvider";
import { ViewerActionsContext, useViewerActions, type ViewerActions } from "./actions";
import type { Hidden } from "./hidden";

/** What a playlist viewer keeps while it is away: the entry it showed and where each entry's video was. */
type Remembered = { entry?: string; positions: Record<string, unknown> };

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

function rememberedOf(saved: unknown): Remembered {
  const value = saved as Partial<Remembered> | undefined;
  return {
    entry: typeof value?.entry === "string" ? value.entry : undefined,
    positions: value?.positions ?? {},
  };
}

/**
 * Shows a playlist: its entries one at a time in a video viewer, with buttons and a menu to move between them.
 * Hidden entries are skipped; each entry opens where it was left.
 */
export function PlaylistViewer({ content, memory, hidden }: ViewerProps<"Playlist">) {
  const { entries } = content;
  const saved = useRef(rememberedOf(memory.saved));
  const [chosen, setChosen] = useState(saved.current.entry);
  // What the entry's own viewer offers (play, step, speed) stays reachable next to the playlist's commands.
  const inner = useRef<ViewerActions>({});

  const index = (() => {
    const found = entries.findIndex((entry) => entry.id === chosen);
    return found >= 0 ? found : entries.findIndex((entry) => !hidden.entry(entry.id));
  })();
  const current = entries[index] as EntryInfo | undefined;
  const previous = adjacentEntry(entries, hidden, index, -1);
  const next = adjacentEntry(entries, hidden, index, 1);

  const choose = (id: string) => {
    saved.current = { ...saved.current, entry: id };
    memory.save(saved.current);
    setChosen(id);
  };
  const own = useMemo<ViewerActions>(
    () => ({
      ...(previous && { "previous-entry": () => choose(previous.id) }),
      ...(next && { "next-entry": () => choose(next.id) }),
    }),
    // `choose` only reads refs and stable setters.
    [previous?.id, next?.id],
  );
  const actions = useMemo<ViewerActions>(
    () =>
      new Proxy(own, {
        get: (target, id) =>
          typeof id === "string" ? (target[id] ?? inner.current[id]) : undefined,
      }),
    [own],
  );
  useViewerActions(actions);

  // An entry the user opened from the workspace tree is shown even if it is hidden.
  const { view, store } = useWorkspace();
  const { requested } = view;
  useEffect(() => {
    if (requested === null || !entries.some((entry) => entry.id === requested)) return;
    choose(requested);
    store.fulfil();
    // `choose` only reads refs and stable setters.
  }, [requested, entries, store]);

  if (!current)
    return (
      <p role="status" className="m-auto p-xl text-muted">
        {entries.length === 0 ? "This playlist has no entries" : "All entries are hidden"}
      </p>
    );
  const listed = entries.filter((entry) => entry.id === current.id || !hidden.entry(entry.id));
  const video = {
    kind: "SeekableVideo" as const,
    id: current.id,
    display_name: current.label,
    entry: current,
  };
  const position = {
    saved: saved.current.positions[current.id],
    save: (state: unknown) => {
      saved.current = {
        ...saved.current,
        positions: { ...saved.current.positions, [current.id]: state },
      };
      memory.save(saved.current);
    },
  };
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-row shrink-0 items-center border-b border-line bg-panel">
        <IconButton
          icon="chevron-left"
          label="Previous entry"
          disabled={!previous}
          onClick={() => previous && choose(previous.id)}
          className="size-row disabled:text-disabled disabled:hover:bg-transparent"
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
          disabled={!next}
          onClick={() => next && choose(next.id)}
          className="size-row disabled:text-disabled disabled:hover:bg-transparent"
        />
        <output aria-label="Entry" className="px-m text-muted tabular-nums">
          {index + 1} / {entries.length}
        </output>
      </div>
      <ViewerActionsContext.Provider value={inner}>
        <VideoViewer key={current.id} content={video} memory={position} hidden={hidden} />
      </ViewerActionsContext.Provider>
    </div>
  );
}
