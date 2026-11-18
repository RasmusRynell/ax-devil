import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { View } from "../generated/api";
import type { Say } from "./useRowActions";
import type { Hidden } from "../viewer/hidden";
import type { PaneTarget } from "./PaneMenu";
import { entriesOf, shownEntry } from "../viewer/shownEntry";
import type { Viewer } from "../viewer/useViewers";

/** Why a header action failed, and what it acted on. */
export type Failure = PaneTarget & { message: string };

/** What the window shows: its viewers, the entry each one's view names, and what the user hid. */
type Shown = { viewers: readonly Viewer[]; views: View["viewers"]; hidden: Hidden };

/** The entry a viewer shows, as `shownEntry` chooses it: `null` for a live stream or an item not resolved. */
function shownEntryId({ item, id }: Viewer, views: View["viewers"], hidden: Hidden): string | null {
  const { resolution } = item;
  if (resolution.status !== "Resolved") return null;
  return shownEntry(entriesOf(resolution.content), hidden, views[id]?.entry)?.id ?? null;
}

/** Whether the pane `target` names still shows the item and the entry it was acting on. */
const shows = ({ viewers, views, hidden }: Shown, { viewer, item, entry }: PaneTarget): boolean => {
  const found = viewers.find((shown) => shown.id === viewer);
  return found?.item.id === item && shownEntryId(found, views, hidden) === entry;
};

/**
 * The one failure of a header action the area shows, in the pane it acted on. An action reports through the `Say` that
 * `begin` returns. Only one action is live at a time: the newest. It stops being live, for good, when anything leaves
 * what it acted on (its pane goes, or shows another item or entry, even for one render and even if it comes back: viewer
 * ids are reused), when another action starts, on unmount, and when it reports. A result of an action that is not
 * live is dropped, so a late result cannot show in another pane, resurrect in a pane that replaced it, nor clear a newer
 * failure. Dismiss, Escape and a menu opening only take down the failure on show: an action still waiting for its answer
 * reports its failure when it comes. The failure shown is also reset when its pane goes or shows another item or entry.
 */
export function useHeaderFailure(
  viewers: readonly Viewer[],
  views: View["viewers"],
  hidden: Hidden,
) {
  const [failure, setFailure] = useState<Failure | null>(null);
  const started = useRef(0);
  // The live action: its number, and what it acts on; `null` when none is.
  const live = useRef<{ mine: number; target: PaneTarget } | null>(null);
  const shown = useRef<Shown>({ viewers, views, hidden });
  useLayoutEffect(() => {
    shown.current = { viewers, views, hidden };
  });
  const dismiss = useCallback(() => setFailure(null), []);
  const begin = useCallback((target: PaneTarget): Say => {
    const mine = ++started.current;
    live.current = { mine, target };
    setFailure(null);
    return (message) => {
      // Dropped on purpose when the user left the context it was for (the pane went, or shows another entry), or a
      // newer action took over: the refused item is still there in the sidebar tree for them to act on.
      if (live.current?.mine !== mine || !shows(shown.current, target)) return;
      live.current = null;
      setFailure(message === null ? null : { ...target, message });
    };
  }, []);
  // Each committed render: what the live action or the failure acts on must still be shown, else it is over.
  useEffect(() => {
    const now = { viewers, views, hidden };
    if (live.current && !shows(now, live.current.target)) live.current = null;
    if (failure && !shows(now, failure)) setFailure(null);
  }, [failure, viewers, views, hidden]);
  useEffect(() => {
    const leave = (event: KeyboardEvent) => event.key === "Escape" && dismiss();
    document.addEventListener("keydown", leave);
    return () => {
      document.removeEventListener("keydown", leave);
      live.current = null;
    };
  }, [dismiss]);
  return { failure, begin, clear: dismiss };
}
