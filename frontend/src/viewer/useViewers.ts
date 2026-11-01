import { useEffect, useMemo } from "react";
import type { ItemState } from "../generated/api";
import { useCan, useDoc, useFollowing, useSetView, useView } from "../session/session";

/** The items with a viewer open, in the order they were added, and the one in front. */
export function useViewers(): { items: readonly ItemState[]; active: ItemState | undefined } {
  const doc = useDoc();
  const items = useMemo(() => doc?.workspace.items.filter((item) => item.open) ?? [], [doc]);
  const [active] = useView("active");
  return { items, active: items.find((item) => item.id === active) };
}

/**
 * Keeps a viewer in front for a window that decides what it shows and may open them: when the Doc has a viewer
 * open that the window has not shown yet it comes to the front, and when the front one closed the last one does.
 * A following or watching window shows whatever the presenter's view says.
 */
export function useFrontViewer(): void {
  const { items, active } = useViewers();
  const setView = useSetView();
  const content = useCan("content");
  const following = useFollowing();
  const front = active?.id;
  useEffect(() => {
    if (!content || following) return;
    setView((shown) => {
      const fresh = items.filter((item) => !shown.viewers[item.id]);
      const shownFront = items.some((item) => item.id === shown.active)
        ? shown.active
        : items.at(-1)?.id;
      const active = fresh.at(-1)?.id ?? shownFront;
      if (fresh.length === 0 && active === shown.active) return shown;
      const opened = Object.fromEntries(fresh.map((item) => [item.id, {}]));
      return { ...shown, active, viewers: { ...shown.viewers, ...opened } };
    });
  }, [content, following, items, front, setView]);
}
