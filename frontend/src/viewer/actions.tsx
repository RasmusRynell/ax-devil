import { createContext, useContext, useEffect, type Dispatch, type SetStateAction } from "react";

/** Commands of the viewer that has the screen, by command id; the shell runs them for its shortcuts and menus. */
export type ViewerActions = Readonly<Record<string, () => void>>;

/** No viewer commands. */
export const NO_ACTIONS: ViewerActions = {};

/** Where a viewer offers its commands; the shell holds them as state so its menus follow the viewer. */
export const ViewerActionsContext = createContext<Dispatch<SetStateAction<ViewerActions>> | null>(
  null,
);

/** Offers a viewer's commands to the shell while it is mounted, and returns them. `actions` must be stable between renders. */
export function useViewerActions(actions: ViewerActions): ViewerActions {
  const offer = useContext(ViewerActionsContext);
  useEffect(() => {
    if (!offer) return;
    offer(actions);
    return () => offer((now) => (now === actions ? NO_ACTIONS : now));
  }, [offer, actions]);
  return actions;
}
