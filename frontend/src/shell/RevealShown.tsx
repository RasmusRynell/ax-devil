import { useLayoutEffect, useMemo } from "react";
import type { SidebarView } from "../generated/api";
import { useStreaming } from "../live/cameras";
import { useDoc, useView } from "../session/session";
import { useFrontItem } from "../viewer/useViewers";
import { useKept } from "./keptState";
import { NO_FOLDS, revealShown } from "./reveal";
import { treeOf } from "./treeModel";

/** What a tree view keeps of its folds. */
export const useFolds = (view: SidebarView) => useKept(`${view}:folds`, NO_FOLDS);

/**
 * Brings a tree view's folds to what the front viewer shows: the rows of a newly shown item are revealed and those
 * of an item the viewer moved on from close. The window keeps this mounted, so a view that is away (another view, or
 * no sidebar) still sees every change as the reference's trees do.
 */
export function RevealShown({ view }: { view: SidebarView }): null {
  const workspace = useDoc()?.workspace;
  const tree = useMemo(() => (workspace ? treeOf(workspace) : []), [workspace]);
  const front = useFrontItem() ?? undefined;
  const [viewers] = useView("viewers");
  const [active] = useView("active");
  const entry = active === undefined ? undefined : viewers[active]?.entry;
  const [held, setHeld] = useFolds(view);
  // Before paint, so a row shown never appears folded for a frame.
  useLayoutEffect(() => {
    const next = revealShown(tree, front, held, entry);
    if (next !== held) setHeld(next);
  }, [tree, front, entry]);
  return null;
}

/** The Live view's sections a stream in front is open in, and those it unfolded. */
type Track = { opened: ReadonlySet<string>; revealed: ReadonlySet<string> };
const NO_TRACK: Track = { opened: new Set(), revealed: new Set() };

/** What the Live view keeps of its section reveals. */
export const useLiveTrack = () => useKept("live:track", NO_TRACK);

/** Unfolds the Live section when a stream comes to the front, as `RevealShown` does for the trees. */
export function RevealLive(): null {
  const open = useStreaming() > 0;
  const [track, setTrack] = useLiveTrack();
  useLayoutEffect(() => {
    if (open === track.opened.has("live")) return;
    const opened = new Set(open ? ["live"] : []);
    setTrack({ opened, revealed: opened });
  }, [open]);
  return null;
}
