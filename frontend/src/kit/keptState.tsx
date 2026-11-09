import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";

type Store = {
  values: Readonly<Record<string, unknown>>;
  set: (key: string, update: (now: unknown) => unknown) => void;
};

const Kept = createContext<Store | null>(null);

/**
 * Keeps what the sidebar's views hold (search, sort, folds) while the window lives, so switching views or hiding the
 * sidebar does not clear it. Window-local: not saved and not shared with a following window.
 */
export function KeptStateProvider({ children }: { children: ReactNode }) {
  const [values, setValues] = useState<Readonly<Record<string, unknown>>>({});
  const set = useCallback(
    (key: string, update: (now: unknown) => unknown) =>
      setValues((now) => {
        const next = update(now[key]);
        return Object.is(next, now[key]) ? now : { ...now, [key]: next };
      }),
    [],
  );
  const store = useMemo(() => ({ values, set }), [values, set]);
  return <Kept.Provider value={store}>{children}</Kept.Provider>;
}

/**
 * `useState` whose value outlives the component, under `key`. `initial` is a constant, since it stands for the value
 * until the first change.
 */
export function useKept<T>(key: string, initial: T): [T, Dispatch<SetStateAction<T>>] {
  const store = useContext(Kept);
  if (!store) throw new Error("useKept needs a KeptStateProvider");
  const { values, set } = store;
  const value = key in values ? (values[key] as T) : initial;
  const update = useCallback<Dispatch<SetStateAction<T>>>(
    (next) =>
      set(key, (now) =>
        typeof next === "function"
          ? (next as (was: T) => T)(now === undefined ? initial : (now as T))
          : next,
      ),
    [set, key, initial],
  );
  return [value, update];
}
