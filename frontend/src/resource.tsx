import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { apiFetch } from "./api";
import type { ApiError, Topic } from "./generated/api";

/** The revision of each topic the backend last announced on the Session connection. */
class Revisions {
  #held = new Map<Topic, number>();
  #listeners = new Set<() => void>();

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  get = (topic: Topic): number | undefined => this.#held.get(topic);

  set = (topic: Topic, revision: number): void => {
    if (this.#held.get(topic) === revision) return;
    this.#held.set(topic, revision);
    this.#listeners.forEach((listener) => listener());
  };
}

const RevisionsContext = createContext<Revisions | null>(null);

/** Holds what the backend announced as changed, for the `useResource` hooks below it and the Session connection that reports it. */
export function ChangesProvider({ children }: { children: ReactNode }) {
  const [revisions] = useState(() => new Revisions());
  return <RevisionsContext value={revisions}>{children}</RevisionsContext>;
}

/** Where the Session connection reports each `Changed` notice; `null` outside a `ChangesProvider`. */
export function useNoticeSink(): ((topic: Topic, revision: number) => void) | null {
  return useContext(RevisionsContext)?.set ?? null;
}

const never = () => () => {};

function useRevision(topic: Topic): number | undefined {
  const revisions = useContext(RevisionsContext);
  return useSyncExternalStore(revisions?.subscribe ?? never, () => revisions?.get(topic));
}

/** Why a request failed: the backend's refusal, else the error's own message. */
export const refusalOf = (error: unknown): ApiError =>
  typeof error === "object" && error !== null && "reason" in error
    ? (error as ApiError)
    : { reason: error instanceof Error ? error.message : String(error), location: "" };

/** Asks the backend and reads the JSON answer; a failure throws the backend's refusal, or one naming the status. */
export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    throw error ?? { reason: `the backend answered ${response.status}`, location: "" };
  }
  return (await response.json()) as T;
}

const readJson = <T,>(path: string, signal: AbortSignal) => fetchJson<T>(path, { signal });

interface Options<T> {
  /** Reads the answer at `path`; the default is `fetchJson`. */
  read?: (path: string, signal: AbortSignal) => Promise<T>;
  /** The revision a value already reflects, for an answer that carries its own, so a notice of that revision asks for nothing. */
  revisionOf?: (value: T) => number;
}

/** What `useResource` gives. */
export interface Resource<T> {
  /** The last answer; `undefined` until there is one, and while `path` is `null`. */
  value: T | undefined;
  /** Why the last read failed, until one succeeds; the last value stays. */
  error: unknown;
  /** Shows a value the caller got elsewhere (a save's answer) instead of any read still pending, which it drops. */
  apply: (value: T) => void;
  /** Reads again now. */
  reload: () => void;
}

/**
 * The answer of `path`, read when the component mounts or `path` changes and again whenever the backend announces
 * another revision of `topic` (also after a reconnect, and a restarted backend counts as one). An answer that a later
 * read or `apply` overtook is dropped. `path` is `null` while the answer is not wanted.
 */
export function useResource<T>(
  topic: Topic,
  path: string | null,
  given: Options<T> = {},
): Resource<T> {
  const noticed = useRevision(topic);
  const [shown, setShown] = useState<{ path: string | null; value?: T; error?: unknown }>({
    path: null,
  });
  // The path and revision of the answer held, and the read that may still answer.
  const held = useRef<{ path: string; revision: number | undefined } | null>(null);
  const latest = useRef<AbortController | null>(null);
  const options = useRef(given);
  const [reloads, setReloads] = useState(0);
  useEffect(() => {
    options.current = given;
  });
  useEffect(() => {
    if (path === null) {
      latest.current?.abort();
      held.current = null;
      return;
    }
    if (held.current?.path === path && held.current.revision === noticed) return;
    latest.current?.abort();
    const request = new AbortController();
    latest.current = request;
    const { read = readJson<T>, revisionOf } = options.current;
    read(path, request.signal).then(
      (value) => {
        if (request.signal.aborted) return;
        held.current = { path, revision: revisionOf?.(value) ?? noticed };
        setShown({ path, value });
      },
      (error: unknown) => {
        if (request.signal.aborted) return;
        setShown((now) => ({ path, value: now.path === path ? now.value : undefined, error }));
      },
    );
  }, [path, noticed, reloads]);
  useEffect(() => () => latest.current?.abort(), []);
  const apply = useCallback(
    (value: T) => {
      if (path === null) return;
      latest.current?.abort();
      held.current = { path, revision: options.current.revisionOf?.(value) ?? noticed };
      setShown({ path, value });
    },
    [path, noticed],
  );
  const reload = useCallback(() => {
    held.current = null;
    setReloads((count) => count + 1);
  }, []);
  const current = shown.path === path ? shown : null;
  return { value: current?.value, error: current?.error, apply, reload };
}
