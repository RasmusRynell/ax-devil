import { useCallback, useEffect, useMemo } from "react";
import type { ItemState } from "../generated/api";
import { useCan, useCommand, useDoc, useFollowing, useSetView, useView } from "../session/session";
import { hiddenOf, NOTHING_HIDDEN, type Hidden } from "./hidden";

/** The items with a viewer open, in the order they were added, and the one in front. */
export function useViewers(): { items: readonly ItemState[]; active: ItemState | undefined } {
  const doc = useDoc();
  const items = useMemo(() => doc?.workspace.items.filter((item) => item.open) ?? [], [doc]);
  const [active] = useView("active");
  return { items, active: items.find((item) => item.id === active) };
}

/**
 * Keeps a viewer in front for a window that decides what it shows: when the front one closed, the last open one
 * takes its place. A window that may also open viewers brings one it has not shown yet to the front. A following or
 * watching window shows whatever the presenter's view says.
 */
export function useFrontViewer(): void {
  const { items, active } = useViewers();
  const setView = useSetView();
  const decides = useCan("view");
  const opens = useCan("content");
  const following = useFollowing();
  const front = active?.id;
  useEffect(() => {
    if (!decides || following) return;
    setView((shown) => {
      const fresh = opens ? items.filter((item) => !shown.viewers[item.id]) : [];
      const shownFront = items.some((item) => item.id === shown.active)
        ? shown.active
        : items.at(-1)?.id;
      const active = fresh.at(-1)?.id ?? shownFront;
      if (fresh.length === 0 && active === shown.active) return shown;
      const opened = Object.fromEntries(fresh.map((item) => [item.id, {}]));
      return { ...shown, active, viewers: { ...shown.viewers, ...opened } };
    });
  }, [decides, opens, following, items, front, setView]);
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
 * open the viewer.
 */
export function useShow(): (item: string, entry?: string) => Promise<void> {
  const doc = useDoc();
  const command = useCommand();
  const setView = useSetView();
  const opens = useCan("content");
  return useCallback(
    async (item, entry) => {
      const state = doc?.workspace.items.find((candidate) => candidate.id === item);
      if (!state) return;
      if (!state.open) {
        if (!opens) return;
        await command({ type: "content", command: { type: "openViewer", item } });
      }
      setView((shown) => {
        const was = shown.viewers[item];
        // Another entry starts from its own beginning: the clock the viewer left is not the new entry's.
        const viewer = entry === undefined || was?.entry === entry ? (was ?? {}) : { entry };
        if (shown.active === item && was === viewer) return shown;
        return { ...shown, active: item, viewers: { ...shown.viewers, [item]: viewer } };
      });
    },
    [doc, command, setView, opens],
  );
}
