import { useLayoutEffect, useMemo } from "react";
import type { SidebarView } from "../generated/api";
import { useDoc } from "../session/session";
import { useViewers } from "../viewer/useViewers";
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
  const front = useViewers().active?.id;
  const [held, setHeld] = useFolds(view);
  // Before paint, so a row shown never appears folded for a frame.
  useLayoutEffect(() => {
    const next = revealShown(tree, front, held);
    if (next !== held) setHeld(next);
  }, [tree, front]);
  return null;
}
