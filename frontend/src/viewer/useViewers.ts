import { useCallback, useEffect, useMemo, useRef } from "react";
import type { ItemState, View } from "../generated/api";
import {
  useCan,
  useCommand,
  useDoc,
  useFollowing,
  useReadView,
  useSetView,
  useView,
} from "../session/session";
import { MAX_PANES } from "../session/views";
import { hiddenOf, NOTHING_HIDDEN, type Hidden } from "./hidden";
import { leaves } from "./layout";
import { autoPlace, close, leftWithout, open as openIn, openToSide, reuse } from "./placement";
import { hold, isHeld } from "./placing";

/** An open viewer: its id in the window's view, the item it shows and whether it is pinned. */
export interface Viewer {
  id: number;
  item: ItemState;
  pinned: boolean;
}

/**
 * The viewers the layout places whose item the Doc has open, by id, and the one in front. A viewer of an item the Doc
 * does not have open is left out.
 */
export function useViewers(): { viewers: readonly Viewer[]; active: Viewer | undefined } {
  const doc = useDoc();
  const [shown] = useView("viewers");
  const [front] = useView("active");
  const [layout] = useView("layout");
  const items = doc?.workspace.items;
  const viewers = useMemo(() => {
    const placed = new Set(leaves(layout));
    return Object.entries(shown).flatMap(([id, viewer]) => {
      const item = items?.find((candidate) => candidate.open && candidate.id === viewer?.item);
      return item && viewer && placed.has(Number(id))
        ? [{ id: Number(id), item, pinned: viewer.pinned }]
        : [];
    });
  }, [shown, layout, items]);
  return { viewers, active: viewers.find((viewer) => viewer.id === front) };
}

/** The item of the viewer in front, if any. */
export function useFrontItem(): string | null {
  return useViewers().active?.item.id ?? null;
}

/** The items that have a viewer, in front or not. */
export function useShownItems(): ReadonlySet<string> {
  const { viewers } = useViewers();
  return useMemo(() => new Set(viewers.map(({ item }) => item.id)), [viewers]);
}

/** Asks the Doc to close `items`; a refusal leaves them open. */
function closeItems(
  command: ReturnType<typeof useCommand>,
  items: readonly string[],
): Promise<unknown> {
  const release = hold(items);
  return Promise.all(
    items.map((item) =>
      command({ type: "content", command: { type: "closeViewer", item } }).catch(() => undefined),
    ),
  ).finally(release);
}

/**
 * Keeps a viewer in front for a window that decides what it shows: when the front one closed, the first in the layout
 * takes its place. A window that may also open viewers gives a viewer to every open item that has none (but those
 * its own commands are placing) beside the largest pane, in front; an item that finds the window full is closed
 * again and `onFull` told. A following or watching window shows whatever the presenter's view says.
 */
export function useFrontViewer(onFull?: () => void): void {
  const doc = useDoc();
  const open = useMemo(() => doc?.workspace.items.filter((item) => item.open) ?? [], [doc]);
  const [front] = useView("active");
  const [viewers] = useView("viewers");
  const [layout] = useView("layout");
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
          if (placed.has(item.id) || isHeld(item.id)) continue;
          const added = autoPlace(next, item.id);
          if (added) next = added;
          else full.push(item.id);
        }
      const live = leaves(next.layout).filter((id) =>
        open.some((item) => item.id === next.viewers[id]?.item),
      );
      if (next.active !== undefined && live.includes(next.active)) return next;
      return live[0] === undefined ? next : { ...next, active: live[0] };
    });
    closing.current = new Set([...closing.current].filter((id) => open.some((i) => i.id === id)));
    const fresh = full.filter((id) => !closing.current.has(id));
    for (const item of fresh) closing.current.add(item);
    if (fresh.length > 0) {
      void closeItems(command, fresh);
      told.current?.();
    }
  }, [decides, opens, following, open, front, viewers, layout, setView, command]);
}

/** What the user hid in the workspace tree, as the Doc's rows say. */
export function useHidden(): Hidden {
  const rows = useDoc()?.workspace.rows;
  return useMemo(() => (rows ? hiddenOf(rows) : NOTHING_HIDDEN), [rows]);
}

/** How `useShow` places the viewer. */
export interface Show {
  /** Open to the Side: a pinned viewer to the right of the front pane, never in place of one. */
  side?: boolean;
  /** Bring forward a viewer already showing the item and entry instead of opening another. */
  reuse?: boolean;
}

/**
 * The way to show an item: its viewer opens if it is not open (when this seat may) and is placed as the preview rule
 * says (see `placement.ts`), showing, given an entry of the item's Content, that one. The entry belongs to the viewer
 * in the window's view, so it survives a reload and a following window shows it too. A preview it replaces that is
 * the last viewer of its item closes that item. Rejects with `CommandRefused` when the backend refuses to open the
 * viewer, with nothing changed, and with the cap on panes when the window has as many as it may.
 */
export function useShow(): (item: string, entry?: string, how?: Show) => Promise<void> {
  const doc = useDoc();
  const command = useCommand();
  const setView = useSetView();
  const readView = useReadView();
  const opens = useCan("content");
  return useCallback(
    async (item, entry, how = {}) => {
      const state = doc?.workspace.items.find((candidate) => candidate.id === item);
      if (!state) return;
      const place = (view: View) =>
        (how.reuse && reuse(view, item, entry)) ||
        (how.side ? openToSide : openIn)(view, item, entry);
      const full = () => new Error(`Cap at ${MAX_PANES} panes`);
      const release = hold([item]);
      try {
        // The viewer is placed only once the Doc has opened it, so a refusal leaves the window as it was.
        if (!state.open) {
          if (!opens) return;
          // [AI] A window with no room for the pane asks the Doc for nothing, so the item never opens and closes again.
          if (!place(readView())) throw full();
          await command({ type: "content", command: { type: "openViewer", item } });
        }
        let refused = false;
        let left: string[] = [];
        setView((shown) => {
          const next = place(shown);
          refused = !next;
          left = next ? leftWithout(shown, next) : [];
          return next ?? shown;
        });
        if (refused) {
          // The window filled up meanwhile: what the Doc opened closes again.
          if (!state.open) await closeItems(command, [item]);
          throw full();
        }
        if (opens && left.length > 0) await closeItems(command, left);
      } finally {
        release();
      }
    },
    [doc, command, setView, readView, opens],
  );
}

/**
 * The way to close panes: they leave the view and, for each item left without one, the Doc closes the item, as
 * closing a pane releases its viewer.
 */
export function useClosePanes(): (viewers: readonly number[]) => void {
  const command = useCommand();
  const setView = useSetView();
  const opens = useCan("content");
  return useCallback(
    (viewers) => {
      let left: string[] = [];
      setView((shown: View) => {
        const next = close(shown, viewers);
        left = leftWithout(shown, next);
        return next;
      });
      if (opens && left.length > 0) void closeItems(command, left);
    },
    [command, setView, opens],
  );
}
