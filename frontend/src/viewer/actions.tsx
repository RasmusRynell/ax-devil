import {
  createContext,
  useContext,
  useEffect,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";

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

/** Whether the pane around a viewer is the active one; a viewer outside any pane counts as active. */
export const PaneActiveContext = createContext(true);

/**
 * Holds the commands of the viewer inside one pane and forwards them to the shell only while the pane is `active`, so
 * a shortcut reaches the pane in front and no other. A pane that stops being active takes its commands back.
 */
export function PaneActions({ active, children }: { active: boolean; children: ReactNode }) {
  const shell = useContext(ViewerActionsContext);
  const [own, setOwn] = useState<ViewerActions>(NO_ACTIONS);
  useEffect(() => {
    if (!shell || !active) return;
    shell(own);
    return () => shell((now) => (now === own ? NO_ACTIONS : now));
  }, [shell, active, own]);
  return (
    <PaneActiveContext.Provider value={active}>
      <ViewerActionsContext.Provider value={setOwn}>{children}</ViewerActionsContext.Provider>
    </PaneActiveContext.Provider>
  );
}

/** Where a viewer sets the muted text its pane's header shows beside the title; a viewer outside any pane has none. */
export const HeaderDetailsContext = createContext<((text: string) => void) | null>(null);

/** Shows `text` in the header of the pane around the viewer while it is mounted. */
export function useHeaderDetails(text: string): void {
  const show = useContext(HeaderDetailsContext);
  useEffect(() => {
    show?.(text);
    return () => show?.("");
  }, [show, text]);
}
