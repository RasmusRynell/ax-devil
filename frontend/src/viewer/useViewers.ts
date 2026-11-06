import { useCallback, useEffect, useMemo, useRef } from "react";
import type { ItemState } from "../generated/api";
import {
  useCan,
  useCommand,
  useDoc,
  useFollowing,
  usePanes,
  useSetView,
  useView,
} from "../session/session";
import { MAX_PANES, withViewer } from "../session/views";
import { hiddenOf, NOTHING_HIDDEN, type Hidden } from "./hidden";

/** An open viewer: its id in the window's view and the item it shows. */
export interface Viewer {
  id: number;
  item: ItemState;
}

/** The open viewers, by id, and the one in front. A viewer of an item the Doc does not have open is left out. */
export function useViewers(): { viewers: readonly Viewer[]; active: Viewer | undefined } {
  const doc = useDoc();
  const [shown] = useView("viewers");
  const [front] = useView("active");
  const items = doc?.workspace.items;
  const viewers = useMemo(
    () =>
      Object.entries(shown).flatMap(([id, viewer]) => {
        const item = items?.find((candidate) => candidate.open && candidate.id === viewer?.item);
        return item ? [{ id: Number(id), item }] : [];
      }),
    [shown, items],
  );
  return { viewers, active: viewers.find((viewer) => viewer.id === front) };
}

/** The item of the viewer in front, if any. */
export function useFrontItem(): string | null {
  return useViewers().active?.item.id ?? null;
}

/**
 * Keeps a viewer in front for a window that decides what it shows: when the front one closed, the last open one
 * takes its place. A window that may also open viewers gives a viewer to every open item that has none and brings
 * the newest to the front; an item that finds the window full is closed again and `onFull` told. A following or
 * watching window shows whatever the presenter's view says.
 */
export function useFrontViewer(onFull?: () => void): void {
  const doc = useDoc();
  const open = useMemo(() => doc?.workspace.items.filter((item) => item.open) ?? [], [doc]);
  const [front] = useView("active");
  const [viewers] = useView("viewers");
  const setView = useSetView();
  const command = useCommand();
  const decides = useCan("view");
  const opens = useCan("content");
  const following = useFollowing();
  // The items asked to close that the Doc still has open, so a second pass does not ask again.
  const closing = useRef(new Set<string>());
  const told = useRef(onFull);
  told.current = onFull;
  useEffect(() => {
    if (!decides || following) return;
    const full: string[] = [];
    setView((shown) => {
      let next = shown;
      const placed = new Set(Object.values(shown.viewers).map((viewer) => viewer?.item));
      if (opens)
        for (const item of open) {
          if (placed.has(item.id)) continue;
          const added = withViewer(next, item.id);
          if (added) next = added;
          else full.push(item.id);
        }
      const live = Object.entries(next.viewers)
        .filter(([, viewer]) => open.some((item) => item.id === viewer?.item))
        .map(([id]) => Number(id));
      if (next.active !== undefined && live.includes(next.active)) return next;
      const last = live.at(-1);
      return last === undefined ? next : { ...next, active: last };
    });
    closing.current = new Set([...closing.current].filter((id) => open.some((i) => i.id === id)));
    for (const item of full.filter((id) => !closing.current.has(id))) {
      closing.current.add(item);
      void command({ type: "content", command: { type: "closeViewer", item } }).catch(
        () => undefined,
      );
      told.current?.();
    }
  }, [decides, opens, following, open, front, viewers, setView, command]);
}

/** What the user hid in the workspace tree, as the Doc's rows say. */
export function useHidden(): Hidden {
  const rows = useDoc()?.workspace.rows;
  return useMemo(() => (rows ? hiddenOf(rows) : NOTHING_HIDDEN), [rows]);
}

/**
 * The way to show an item: its viewer opens if it is not open (when this seat may), comes to the front, and, given an
 * entry of the item's Content, shows that one. The entry belongs to the item's viewer in the window's view, so it
 * survives a reload and a following window shows it too. Rejects with `CommandRefused` when the backend refuses to
 * open the viewer, and with the cap on panes when the window has as many as it may.
 */
export function useShow(): (item: string, entry?: string) => Promise<void> {
  const doc = useDoc();
  const command = useCommand();
  const setView = useSetView();
  const opens = useCan("content");
  const panes = usePanes();
  return useCallback(
    async (item, entry) => {
      const state = doc?.workspace.items.find((candidate) => candidate.id === item);
      if (!state) return;
      const full = new Error(`Cap at ${MAX_PANES} panes`);
      if (!state.open) {
        if (!opens) return;
        if (panes >= MAX_PANES) throw full;
        await command({ type: "content", command: { type: "openViewer", item } });
      }
      let refused = false;
      setView((shown) => {
        const found = Object.entries(shown.viewers).find(([, viewer]) => viewer?.item === item);
        if (!found?.[1]) {
          const added = withViewer(shown, item, entry);
          refused = !added;
          return added ?? shown;
        }
        const [key, was] = found;
        const id = Number(key);
        // Another entry starts from its own beginning: the clock the viewer left is not the new entry's.
        const viewer =
          entry === undefined || was.entry === entry ? was : { item, pinned: was.pinned, entry };
        if (shown.active === id && was === viewer) return shown;
        return { ...shown, active: id, viewers: { ...shown.viewers, [id]: viewer } };
      });
      if (refused) throw full;
    },
    [doc, command, setView, opens, panes],
  );
}
