import {
  createContext,
  useContext,
  useEffect,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { WorkspaceStore, type WorkspaceView } from "./store";

const WorkspaceContext = createContext<WorkspaceStore | null>(null);

function backendStore(): WorkspaceStore {
  return new WorkspaceStore({
    open: () => new EventSource("/api/events"),
    after: (callback, ms) => {
      const timer = setTimeout(callback, ms);
      return () => clearTimeout(timer);
    },
  });
}

/** Follows the backend's workspace events for as long as it is mounted. Tests pass their own store. */
export function WorkspaceProvider({
  store: given,
  children,
}: {
  store?: WorkspaceStore;
  children: ReactNode;
}) {
  const [store] = useState(() => given ?? backendStore());
  useEffect(() => {
    store.start();
    return () => store.stop();
  }, [store]);
  return <WorkspaceContext.Provider value={store}>{children}</WorkspaceContext.Provider>;
}

/** The workspace as the backend last reported it, and the store to open and close viewers through. */
export function useWorkspace(): { view: WorkspaceView; store: WorkspaceStore } {
  const store = useContext(WorkspaceContext);
  if (!store) throw new Error("useWorkspace needs a WorkspaceProvider");
  return { view: useSyncExternalStore(store.subscribe, store.getSnapshot), store };
}
