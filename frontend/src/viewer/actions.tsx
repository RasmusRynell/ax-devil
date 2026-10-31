import { createContext, useContext, useEffect, type RefObject } from "react";

/** Commands of the viewer that has the screen, by command id; the shell runs them for its shortcuts and menus. */
export type ViewerActions = Readonly<Record<string, () => void>>;

export const ViewerActionsContext = createContext<RefObject<ViewerActions> | null>(null);

/** Offers a viewer's commands to the shell while it is mounted. `actions` must be stable between renders. */
export function useViewerActions(actions: ViewerActions): void {
  const registry = useContext(ViewerActionsContext);
  useEffect(() => {
    if (!registry) return;
    registry.current = actions;
    return () => {
      if (registry.current === actions) registry.current = {};
    };
  }, [registry, actions]);
}
