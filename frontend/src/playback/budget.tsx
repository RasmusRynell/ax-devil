/** The frame memory of a window, shared equally by its panes: the equal-share rule the backend keeps for its cache. */

import { createContext, useContext, type ReactNode } from "react";
import { BUFFER_BUDGET_BYTES } from "./sessionDeps";

/** What each of `panes` panes may hold of decoded frames: the window's budget in equal parts, whole bytes. */
export const paneBudget = (panes: number): number =>
  Math.floor(BUFFER_BUDGET_BYTES / Math.max(1, panes));

// Without a provider a viewer is the only one in its window, as in a test.
const Budget = createContext(BUFFER_BUDGET_BYTES);

/**
 * Gives every viewer inside it its share of the window's frame budget, for `panes` open panes of any kind. A pane
 * that opens or closes changes the share, and the viewers resize their frame window to it.
 */
export function PaneBudget({ panes, children }: { panes: number; children: ReactNode }) {
  return <Budget value={paneBudget(panes)}>{children}</Budget>;
}

/** The bytes of decoded frames this viewer may hold now. */
export const usePaneBudget = (): number => useContext(Budget);
